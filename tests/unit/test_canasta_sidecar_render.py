"""Tests for sidecar rendering — config/sidecars.yaml -> runtime artifacts.

Covers the pure render logic (Compose service + k8s values, env resolution)
and the module's write/mirror behavior (remove the compose layer when no
sidecars remain; always emit values-sidecars.yaml on k8s so helm prunes).
"""

import os

import yaml

import canasta_render_sidecars
import canasta_sidecar_render as render
from mock_ansible import run_module_with_params

CACHE = {"name": "cache", "image": "redis:7-alpine",
         "command": "redis-server --maxmemory ${REDIS_MAX_MEMORY:-100mb}",
         "ports": [6379],
         "volumes": [{"name": "data", "mountPath": "/data",
                      "persistent": True, "size": "1Gi"}]}

CITATION = {"name": "citation", "image": "example/citoid:1.0",
            "ports": [1970], "depends_on": ["translator"],
            "env": {"SHARED_SECRET": "${CITATION_SHARED_SECRET:-}"},
            "files": [{"source": "config/citation.yaml",
                       "mountPath": "/srv/config.yaml", "readOnly": True}],
            "healthcheck": {"path": "/_info", "port": 1970},
            "resources": {"memory": "512Mi"}}


class TestResolveEnv:
    def test_variants(self):
        env = {"SET": "val", "EMPTY": ""}
        r = render.resolve_env_value
        assert r("${SET}", env) == "val"
        assert r("${MISSING}", env) == ""
        assert r("${MISSING:-d}", env) == "d"
        assert r("${EMPTY:-d}", env) == "d"      # :- falls back on empty
        assert r("${EMPTY-d}", env) == ""        # - keeps empty
        assert r("pre-${SET}-post", env) == "pre-val-post"


class TestComposeRender:
    def test_image_command_restart(self):
        svc, _ = render.render_compose_service(CACHE)
        assert svc["image"] == "redis:7-alpine"
        # ${VAR} passes through — Compose interpolates from .env itself
        assert "${REDIS_MAX_MEMORY:-100mb}" in svc["command"]
        assert svc["restart"] == "unless-stopped"

    def test_ports_become_expose(self):
        svc, _ = render.render_compose_service(CACHE)
        assert svc["expose"] == ["6379"]

    def test_persistent_volume_named(self):
        svc, vols = render.render_compose_service(CACHE)
        assert "cache-data:/data" in svc["volumes"]
        assert "cache-data" in vols

    def test_ephemeral_volume_is_path_only(self):
        sc = {"name": "x", "image": "i",
              "volumes": [{"name": "t", "mountPath": "/tmp"}]}
        svc, vols = render.render_compose_service(sc)
        assert svc["volumes"] == ["/tmp"]
        assert vols == {}

    def test_build_excludes_image(self):
        svc, _ = render.render_compose_service({"name": "x", "build": "./x"})
        assert svc["build"] == "./x" and "image" not in svc

    def test_files_become_ro_bind_mount(self):
        svc, _ = render.render_compose_service(CITATION)
        assert "./config/citation.yaml:/srv/config.yaml:ro" in svc["volumes"]

    def test_depends_on_and_resources(self):
        svc, _ = render.render_compose_service(CITATION)
        assert svc["depends_on"] == ["translator"]
        assert svc["deploy"]["resources"]["limits"]["memory"] == "512Mi"

    def test_healthcheck_http(self):
        svc, _ = render.render_compose_service(CITATION)
        assert svc["healthcheck"]["test"][:3] == ["CMD", "curl", "-f"]
        assert "1970/_info" in svc["healthcheck"]["test"][3]

    def test_healthcheck_tcp(self):
        svc, _ = render.render_compose_service(
            {"name": "x", "image": "i", "healthcheck": {"tcp": 6379}})
        assert "nc -z localhost 6379" in svc["healthcheck"]["test"][1]

    def test_render_compose_aggregates_and_empty(self):
        out = render.render_compose([CACHE, CITATION])
        assert set(out["services"]) == {"cache", "citation"}
        assert "cache-data" in out["volumes"]
        assert render.render_compose([]) is None


class TestK8sValues:
    def test_env_and_command_resolved(self):
        env = {"REDIS_MAX_MEMORY": "256mb", "CITATION_SHARED_SECRET": "s3cr3t"}
        out = render.render_k8s_values([CACHE, CITATION], env, lambda s: "FILE")
        cache = next(s for s in out if s["name"] == "cache")
        citation = next(s for s in out if s["name"] == "citation")
        assert "256mb" in cache["command"]
        assert citation["env"]["SHARED_SECRET"] == "s3cr3t"

    def test_file_content_inlined(self):
        out = render.render_k8s_values([CITATION], {}, lambda s: "FILE-BODY")
        assert out[0]["files"][0]["content"] == "FILE-BODY"
        assert out[0]["files"][0]["mountPath"] == "/srv/config.yaml"

    def test_empty(self):
        assert render.render_k8s_values([], {}, lambda s: "") == []


def _write_sidecars(inst, sidecars):
    cfg = os.path.join(inst, "config")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "sidecars.yaml"), "w") as handle:
        yaml.dump({"sidecars": sidecars}, handle)


class TestModuleCompose:
    def test_writes_override(self, tmp_dir):
        _write_sidecars(tmp_dir, [CACHE])
        result, failed, _ = run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "compose"})
        assert not failed and result["changed"] is True
        path = os.path.join(tmp_dir, "docker-compose.sidecars.yml")
        with open(path) as handle:
            data = yaml.safe_load(handle)
        assert "cache" in data["services"]

    def test_removes_override_when_empty(self, tmp_dir):
        # An existing layer must be deleted when no sidecars remain (mirror).
        path = os.path.join(tmp_dir, "docker-compose.sidecars.yml")
        with open(path, "w") as handle:
            handle.write("services: {}\n")
        _write_sidecars(tmp_dir, [])
        result, failed, _ = run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "compose"})
        assert not failed and result["changed"] is True
        assert not os.path.exists(path)

    def test_idempotent(self, tmp_dir):
        _write_sidecars(tmp_dir, [CACHE])
        run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "compose"})
        result, _, _ = run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "compose"})
        assert result["changed"] is False


class TestModuleK8s:
    def test_writes_values_with_resolved_env(self, tmp_dir):
        with open(os.path.join(tmp_dir, ".env"), "w") as handle:
            handle.write("REDIS_MAX_MEMORY=256mb\n")
        _write_sidecars(tmp_dir, [CACHE])
        result, failed, _ = run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "kubernetes"})
        assert not failed
        with open(os.path.join(tmp_dir, "values-sidecars.yaml")) as handle:
            data = yaml.safe_load(handle)
        assert "256mb" in data["sidecars"][0]["command"]

    def test_empty_writes_empty_list(self, tmp_dir):
        _write_sidecars(tmp_dir, [])
        run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "kubernetes"})
        path = os.path.join(tmp_dir, "values-sidecars.yaml")
        with open(path) as handle:
            data = yaml.safe_load(handle)
        assert data["sidecars"] == []
