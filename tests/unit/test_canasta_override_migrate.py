"""Tests for migrating docker-compose.override.yml sidecars to sidecars.yaml.

Covers the Compose-service -> agnostic-schema translation (the inverse of the
render), all-or-nothing per service with reporting, the move semantics (remove
migrated services + their volumes from the override), and the module's
dry-run / write behavior.
"""

import os

import yaml

import canasta_override_migrate as m
import canasta_sidecar_migrate
from mock_ansible import run_module_with_params


class TestTranslateService:
    def test_simple_image_and_ports(self):
        sc, reasons, _ = m.translate_service(
            "cache", {"image": "redis:7-alpine", "expose": ["6379"]})
        assert reasons == []
        assert sc == {"name": "cache", "image": "redis:7-alpine",
                      "ports": [6379]}

    def test_ports_extracts_container_side(self):
        sc, _, _ = m.translate_service(
            "x", {"image": "i", "ports": ["127.0.0.1:8080:1970"]})
        assert sc["ports"] == [1970]

    def test_environment_list_and_map(self):
        a, _, _ = m.translate_service(
            "x", {"image": "i", "environment": ["K=v", "FLAG"]})
        b, _, _ = m.translate_service(
            "y", {"image": "i", "environment": {"K": "v", "N": 5}})
        assert a["env"] == {"K": "v", "FLAG": ""}
        assert b["env"] == {"K": "v", "N": "5"}

    def test_bind_mount_becomes_files(self):
        sc, _, _ = m.translate_service("x", {
            "image": "i",
            "volumes": ["./config/app.yaml:/srv/app.yaml:ro"]})
        assert sc["files"] == [{"source": "config/app.yaml",
                                "mountPath": "/srv/app.yaml", "readOnly": True}]
        assert "volumes" not in sc

    def test_named_volume_becomes_persistent_with_assumption(self):
        sc, _, notes = m.translate_service("cache", {
            "image": "i", "volumes": ["data:/data"]})
        assert sc["volumes"] == [{"name": "data", "mountPath": "/data",
                                  "persistent": True, "size": "1Gi"}]
        assert any("assumed size" in n for n in notes)

    def test_healthcheck_cmd_and_shell(self):
        a, _, _ = m.translate_service("x", {
            "image": "i", "healthcheck": {"test": ["CMD", "redis-cli", "ping"]}})
        b, _, _ = m.translate_service("y", {
            "image": "i", "healthcheck": {"test": ["CMD-SHELL", "curl -f x"]}})
        assert a["healthcheck"] == {"command": ["redis-cli", "ping"]}
        assert b["healthcheck"] == {"command": ["sh", "-c", "curl -f x"]}

    def test_deploy_resources(self):
        sc, _, _ = m.translate_service("x", {
            "image": "i",
            "deploy": {"resources": {"limits": {"memory": "512Mi",
                                                "cpus": "0.5"}}}})
        assert sc["resources"] == {"memory": "512Mi", "cpu": "0.5"}

    def test_depends_on_list_and_map(self):
        a, _, _ = m.translate_service(
            "x", {"image": "i", "depends_on": ["translator"]})
        b, _, _ = m.translate_service(
            "y", {"image": "i",
                  "depends_on": {"translator": {"condition": "started"}}})
        assert a["depends_on"] == ["translator"]
        assert b["depends_on"] == ["translator"]

    def test_build_context(self):
        sc, _, _ = m.translate_service("x", {"build": "./svc"})
        assert sc == {"name": "x", "build": "./svc"}

    def test_unsupported_field_blocks(self):
        sc, reasons, _ = m.translate_service(
            "x", {"image": "i", "networks": ["mynet"]})
        assert sc is None
        assert any("networks" in r for r in reasons)

    def test_custom_dockerfile_blocks(self):
        sc, reasons, _ = m.translate_service(
            "x", {"build": {"context": ".", "dockerfile": "Custom.df"}})
        assert sc is None
        assert any("dockerfile" in r for r in reasons)

    def test_deploy_non_resources_blocks(self):
        sc, reasons, _ = m.translate_service(
            "x", {"image": "i", "deploy": {"replicas": 3}})
        assert sc is None
        assert any("deploy.replicas" in r for r in reasons)

    def test_port_range_skips_instead_of_crashing(self):
        sc, reasons, _ = m.translate_service(
            "x", {"image": "i", "ports": ["8000-8005"]})
        assert sc is None
        assert any("unparseable port" in r for r in reasons)

    def test_unparseable_expose_port_skips(self):
        sc, reasons, _ = m.translate_service(
            "x", {"image": "i", "expose": ["${PORT}"]})
        assert sc is None
        assert any("unparseable expose port" in r for r in reasons)


CORE_AND_SIDECARS = {
    "services": {
        "web": {"image": "custom-web"},          # reserved -> stays
        "cache": {"image": "redis:7-alpine", "volumes": ["data:/data"]},
        "bad": {"image": "i", "networks": ["n"]},  # unmodellable -> stays
    },
    "volumes": {"data": None, "unused": None},
}


class TestPlanMigration:
    def test_reserved_stays_unmodellable_stays(self):
        plan = m.plan_migration(CORE_AND_SIDECARS)
        assert plan["migrated"] == ["cache"]
        assert [s["name"] for s in plan["skipped"]] == ["bad"]
        remaining = plan["remaining_override"]["services"]
        assert set(remaining) == {"web", "bad"}  # cache moved out

    def test_unreferenced_volume_dropped(self):
        plan = m.plan_migration(CORE_AND_SIDECARS)
        # 'data' was only used by the migrated cache; 'unused' by nobody.
        assert "volumes" not in plan["remaining_override"]

    def test_existing_name_collision_skipped(self):
        plan = m.plan_migration(
            {"services": {"cache": {"image": "i"}}}, existing_names=["cache"])
        assert plan["migrated"] == []
        assert "already declared" in plan["skipped"][0]["reasons"][0]


def _write(inst, name, data):
    if name == "override":
        path = os.path.join(inst, "docker-compose.override.yml")
    else:
        path = os.path.join(inst, "config", "sidecars.yaml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        yaml.dump(data, handle)


class TestModule:
    def test_dry_run_writes_nothing(self, tmp_dir):
        _write(tmp_dir, "override", {"services": {
            "cache": {"image": "redis:7-alpine"}}})
        result, failed, _ = run_module_with_params(canasta_sidecar_migrate, {
            "instance_path": tmp_dir, "dry_run": True})
        assert not failed
        assert result["migrated"] == ["cache"] and result["changed"] is False
        assert not os.path.exists(os.path.join(tmp_dir, "config",
                                               "sidecars.yaml"))

    def test_migrate_writes_and_moves(self, tmp_dir):
        _write(tmp_dir, "override", {"services": {
            "web": {"image": "custom"},
            "cache": {"image": "redis:7-alpine", "expose": ["6379"]}}})
        result, failed, _ = run_module_with_params(canasta_sidecar_migrate, {
            "instance_path": tmp_dir})
        assert not failed and result["changed"] is True
        with open(os.path.join(tmp_dir, "config", "sidecars.yaml")) as h:
            sidecars = yaml.safe_load(h)["sidecars"]
        assert sidecars[0]["name"] == "cache"
        # cache removed from the override; web (reserved) stays.
        with open(os.path.join(tmp_dir, "docker-compose.override.yml")) as h:
            override = yaml.safe_load(h)
        assert set(override["services"]) == {"web"}

    def test_override_removed_when_empty(self, tmp_dir):
        _write(tmp_dir, "override", {"services": {
            "cache": {"image": "redis:7-alpine"}}})
        run_module_with_params(canasta_sidecar_migrate, {
            "instance_path": tmp_dir})
        assert not os.path.exists(
            os.path.join(tmp_dir, "docker-compose.override.yml"))

    def test_no_override_is_noop(self, tmp_dir):
        result, failed, _ = run_module_with_params(canasta_sidecar_migrate, {
            "instance_path": tmp_dir})
        assert not failed and result["changed"] is False
        assert result["migrated"] == []
