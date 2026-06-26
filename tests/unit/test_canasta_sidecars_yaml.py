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

    def test_env_private_ok(self):
        assert canasta_sidecars_yaml.validate_sidecars(
            [{"name": "c", "image": "i",
              "env": {"TOK": "${T}"}, "envPrivate": ["TOK"]}]) is None

    def test_env_private_not_a_list(self):
        err = canasta_sidecars_yaml.validate_sidecars(
            [{"name": "c", "image": "i",
              "env": {"TOK": "${T}"}, "envPrivate": "TOK"}])
        assert "envPrivate must be a list" in err

    def test_env_private_unknown_key(self):
        err = canasta_sidecars_yaml.validate_sidecars(
            [{"name": "c", "image": "i",
              "env": {"TOK": "${T}"}, "envPrivate": ["TYPO"]}])
        assert "not in env" in err


def _add(inst, **extra):
    """Seed a 'cache' sidecar via import (used by list/query/remove tests)."""
    return run_module_with_params(canasta_sidecars_yaml, {
        "instance_path": inst, "state": "import",
        "definitions": "name: cache\nimage: redis:7-alpine\n"})


class TestModuleStates:
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


class TestParseSidecarDefinitions:
    _p = staticmethod(canasta_sidecars_yaml.parse_sidecar_definitions)

    def test_single_mapping(self):
        assert self._p("name: cache\nimage: redis:7\n") == [
            {"name": "cache", "image": "redis:7"}]

    def test_bare_list(self):
        out = self._p("- {name: a, image: i}\n- {name: b, image: j}\n")
        assert [s["name"] for s in out] == ["a", "b"]

    def test_sidecars_key(self):
        out = self._p("sidecars:\n  - {name: a, image: i}\n")
        assert out == [{"name": "a", "image": "i"}]

    def test_empty(self):
        assert self._p("") == []

    def test_scalar_rejected(self):
        with pytest.raises(ValueError):
            self._p("just-a-string")


class TestImportState:
    def _import(self, inst, text):
        return run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": inst, "state": "import", "definitions": text})

    def test_import_full_sidecar(self, tmp_dir):
        text = ("sidecars:\n"
                "  - name: citation\n"
                "    image: example/citoid:1.0\n"
                "    ports: [1970]\n"
                "    healthcheck: {path: /_info, port: 1970}\n"
                "    depends_on: [translator]\n")
        result, failed, _ = self._import(tmp_dir, text)
        assert not failed and result["changed"] is True
        assert result["names"] == ["citation"]
        sc = canasta_sidecars_yaml.read_sidecars(tmp_dir)[0]
        # Rich fields the flag-based add can't express survive the round-trip.
        assert sc["healthcheck"] == {"path": "/_info", "port": 1970}
        assert sc["depends_on"] == ["translator"]
        assert sc["ports"] == [1970]

    def test_import_multiple(self, tmp_dir):
        text = "- {name: a, image: i}\n- {name: b, image: j}\n"
        result, failed, _ = self._import(tmp_dir, text)
        assert not failed and result["names"] == ["a", "b"]

    def test_import_dup_with_existing_fails(self, tmp_dir):
        _add(tmp_dir)  # adds 'cache'
        _, failed, msg = self._import(tmp_dir, "name: cache\nimage: redis:7\n")
        assert failed and "already exist" in msg

    def test_import_reserved_name_fails(self, tmp_dir):
        _, failed, msg = self._import(tmp_dir, "name: web\nimage: i\n")
        assert failed and "reserved" in msg

    def test_import_missing_image_and_build_fails(self, tmp_dir):
        _, failed, msg = self._import(tmp_dir, "name: c\n")
        assert failed and "image or build" in msg

    def test_import_invalid_yaml_fails(self, tmp_dir):
        _, failed, msg = self._import(tmp_dir, "name: c\n  bad: [unclosed")
        assert failed and "invalid sidecar file" in msg


class TestCommandWiring:
    def test_group_registered_in_cli(self):
        with open(os.path.join(REPO, "canasta.py")) as f:
            assert '"sidecar": ["add", "list", "remove", "migrate"]' in f.read()

    def test_no_param_dest_collides_with_dispatch_var(self):
        # A param internally named 'command' clobbers canasta.py's top-level
        # subparser dest='command' (the dispatch variable), breaking the
        # whole subcommand. No sidecar param may use that internal name.
        with open(os.path.join(REPO, "meta", "command_definitions.yml")) as f:
            defs = yaml.safe_load(f)
        for cmd in ("sidecar_add", "sidecar_list", "sidecar_remove"):
            entry = next(c for c in defs["commands"] if c["name"] == cmd)
            dests = {p["name"] for p in entry.get("parameters", [])}
            assert "command" not in dests

    def test_add_file_read_on_controller_not_target(self):
        # `--file` is a controller-side path. A delegated slurp can read the
        # wrong host after resolve_instance switches the connection for a
        # remote (-H) instance; a lookup always runs on the controller.
        with open(os.path.join(REPO, "playbooks", "sidecar_add.yml")) as f:
            body = f.read()
        assert "lookup('file', file)" in body
        assert "ansible.builtin.slurp" not in body

    def test_command_defs_and_playbooks_exist(self):
        with open(os.path.join(REPO, "meta", "command_definitions.yml")) as f:
            defs = yaml.safe_load(f)
        names = {c["name"] for c in defs["commands"]}
        groups = {g["name"] for g in defs["command_groups"]}
        assert "sidecar" in groups
        for cmd in ("sidecar_add", "sidecar_list", "sidecar_remove",
                    "sidecar_migrate"):
            assert cmd in names
        for playbook in ("sidecar_add.yml", "sidecar_list.yml",
                         "sidecar_remove.yml", "sidecar_migrate.yml"):
            assert os.path.exists(os.path.join(REPO, "playbooks", playbook))
