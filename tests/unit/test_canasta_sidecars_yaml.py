"""Tests for the canasta_sidecars_yaml module + sidecar-name validation.

Covers config/sidecars.yaml CRUD (the storage foundation for the sidecar
primitive) and the validation that keeps a sidecar name from colliding with
a core stack service.
"""

import os

import pytest
import yaml

import canasta_sidecars_yaml
from mock_ansible import run_module_with_params

REPO = os.path.join(os.path.dirname(__file__), "..", "..")
_v = canasta_sidecars_yaml.validate_sidecar_name


class TestValidateSidecarName:
    def test_valid_simple(self):
        assert _v("cache") is None

    def test_valid_with_hyphen(self):
        # Unlike a wiki ID, a sidecar name may contain hyphens (DNS label).
        assert _v("receipt-scanner") is None

    def test_empty(self):
        assert "empty" in _v("")

    def test_uppercase_rejected(self):
        assert _v("Cache") is not None

    def test_leading_hyphen_rejected(self):
        assert _v("-cache") is not None

    def test_trailing_hyphen_rejected(self):
        assert _v("cache-") is not None

    def test_underscore_rejected(self):
        assert _v("my_cache") is not None

    def test_too_long(self):
        assert "too long" in _v("a" * 64)

    def test_reserved_web(self):
        assert "reserved" in _v("web")

    def test_reserved_db(self):
        assert "reserved" in _v("db")

    def test_reserved_caddy(self):
        assert _v("caddy") is not None


class TestParseHelpers:
    def test_parse_env(self):
        assert canasta_sidecars_yaml.parse_env(["A=1", "B=x=y"]) == {
            "A": "1", "B": "x=y"}

    def test_parse_env_bad(self):
        with pytest.raises(ValueError):
            canasta_sidecars_yaml.parse_env(["NOEQUALS"])

    def test_parse_volumes_ephemeral(self):
        assert canasta_sidecars_yaml.parse_volumes(["t:/tmp"]) == [
            {"name": "t", "mountPath": "/tmp", "persistent": False}]

    def test_parse_volumes_persistent(self):
        assert canasta_sidecars_yaml.parse_volumes(["data:/data:1Gi"]) == [
            {"name": "data", "mountPath": "/data",
             "persistent": True, "size": "1Gi"}]

    def test_parse_volumes_bad(self):
        with pytest.raises(ValueError):
            canasta_sidecars_yaml.parse_volumes(["justone"])

    def test_build_sidecar_image_omits_empty(self):
        assert canasta_sidecars_yaml.build_sidecar("cache", image="redis:7") == {
            "name": "cache", "image": "redis:7"}

    def test_build_sidecar_build_excludes_image(self):
        sidecar = canasta_sidecars_yaml.build_sidecar("c", build="./c")
        assert sidecar == {"name": "c", "build": "./c"}

    def test_build_sidecar_full(self):
        sidecar = canasta_sidecars_yaml.build_sidecar(
            "cache", image="redis:7", ports=["6379"], command="redis-server",
            env=["A=1"], volumes=["data:/data:1Gi"])
        assert sidecar["ports"] == [6379]
        assert sidecar["env"] == {"A": "1"}
        assert sidecar["volumes"][0]["persistent"] is True


class TestValidateSidecars:
    def test_ok(self):
        assert canasta_sidecars_yaml.validate_sidecars(
            [{"name": "cache", "image": "redis:7"}]) is None

    def test_duplicate(self):
        err = canasta_sidecars_yaml.validate_sidecars(
            [{"name": "c", "image": "i"}, {"name": "c", "image": "j"}])
        assert "duplicate" in err

    def test_missing_image_and_build(self):
        err = canasta_sidecars_yaml.validate_sidecars([{"name": "c"}])
        assert "image or build" in err

    def test_both_image_and_build(self):
        err = canasta_sidecars_yaml.validate_sidecars(
            [{"name": "c", "image": "i", "build": "./c"}])
        assert "cannot set both" in err

    def test_reserved_name(self):
        err = canasta_sidecars_yaml.validate_sidecars(
            [{"name": "web", "image": "i"}])
        assert "reserved" in err


def _add(inst, **extra):
    params = {"instance_path": inst, "state": "add", "name": "cache",
              "image": "redis:7-alpine"}
    params.update(extra)
    return run_module_with_params(canasta_sidecars_yaml, params)


class TestModuleStates:
    def test_add_writes_file(self, tmp_dir):
        result, failed, _ = _add(tmp_dir)
        assert not failed and result["changed"] is True
        path = os.path.join(tmp_dir, "config", "sidecars.yaml")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["sidecars"][0]["name"] == "cache"

    def test_add_duplicate_fails(self, tmp_dir):
        _add(tmp_dir)
        _, failed, msg = _add(tmp_dir)
        assert failed and "already exists" in msg

    def test_add_reserved_fails(self, tmp_dir):
        _, failed, msg = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "add",
            "name": "web", "image": "i"})
        assert failed and "reserved" in msg

    def test_add_requires_image_or_build(self, tmp_dir):
        _, failed, msg = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "add", "name": "cache"})
        assert failed and "image or build" in msg

    def test_add_image_and_build_mutually_exclusive(self, tmp_dir):
        _, failed, msg = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "add", "name": "cache",
            "image": "i", "build": "./c"})
        assert failed and "mutually exclusive" in msg

    def test_list_after_add(self, tmp_dir):
        _add(tmp_dir, ports=["6379"])
        result, failed, _ = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "list"})
        assert not failed and result["names"] == ["cache"]

    def test_query_exists_and_absent(self, tmp_dir):
        _add(tmp_dir)
        present, _, _ = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "query", "name": "cache"})
        absent, _, _ = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "query", "name": "nope"})
        assert present["exists"] is True and absent["exists"] is False

    def test_remove(self, tmp_dir):
        _add(tmp_dir)
        result, failed, _ = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "remove", "name": "cache"})
        assert not failed and result["changed"] is True
        gone, _, _ = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "query", "name": "cache"})
        assert gone["exists"] is False

    def test_remove_absent_no_change(self, tmp_dir):
        result, failed, _ = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "remove", "name": "nope"})
        assert not failed and result["changed"] is False

    def test_validate_ok(self, tmp_dir):
        _add(tmp_dir)
        result, failed, _ = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "validate"})
        assert not failed and result["valid"] is True

    def test_read_empty_instance(self, tmp_dir):
        result, failed, _ = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": tmp_dir, "state": "read"})
        assert not failed and result["sidecars"] == []


class TestCommandWiring:
    def test_group_registered_in_cli(self):
        with open(os.path.join(REPO, "canasta.py")) as f:
            assert '"sidecar": ["add", "list", "remove"]' in f.read()

    def test_command_defs_and_playbooks_exist(self):
        with open(os.path.join(REPO, "meta", "command_definitions.yml")) as f:
            defs = yaml.safe_load(f)
        names = {c["name"] for c in defs["commands"]}
        groups = {g["name"] for g in defs["command_groups"]}
        assert "sidecar" in groups
        for cmd in ("sidecar_add", "sidecar_list", "sidecar_remove"):
            assert cmd in names
        for playbook in ("sidecar_add.yml", "sidecar_list.yml",
                         "sidecar_remove.yml"):
            assert os.path.exists(os.path.join(REPO, "playbooks", playbook))
