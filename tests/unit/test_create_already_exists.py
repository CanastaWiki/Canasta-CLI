"""Tests for the `create` duplicate-ID pre-flight in canasta.py.

`canasta create` refuses an ID that already exists in the registry. The
refusal is caught in the CLI layer before Ansible runs so it exits with a
distinct code (EXIT_ALREADY_EXISTS = 3) a wrapping script can branch on,
rather than the opaque Ansible task-failure code. The playbook's own
`fail:` in _validate_inputs.yml stays as the backstop.
"""

import argparse
import json
import os
import sys

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402


def _seed_registry(config_dir, instances):
    with open(os.path.join(config_dir, "conf.json"), "w") as f:
        json.dump({"Instances": instances}, f)


def _args(instance_id):
    return argparse.Namespace(id=instance_id)


class TestCreatePrecondition:
    def test_duplicate_id_exits_already_exists(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        _seed_registry(str(tmp_path), {
            "icannwiki": {"path": "/opt/canasta/icannwiki", "host": "localhost"},
        })
        with pytest.raises(SystemExit) as exc:
            canasta.check_create_precondition(_args("icannwiki"))
        assert exc.value.code == canasta.EXIT_ALREADY_EXISTS
        err = capsys.readouterr().err
        assert "already exists in the registry" in err
        assert "/opt/canasta/icannwiki" in err
        assert "canasta delete --id icannwiki" in err

    def test_message_uses_registered_host(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        _seed_registry(str(tmp_path), {
            "prodwiki": {"path": "/srv/prodwiki", "host": "prod1.example.com"},
        })
        with pytest.raises(SystemExit):
            canasta.check_create_precondition(_args("prodwiki"))
        assert "host: prod1.example.com" in capsys.readouterr().err

    def test_host_defaults_to_localhost_when_absent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        _seed_registry(str(tmp_path), {"local": {"path": "/opt/canasta/local"}})
        with pytest.raises(SystemExit):
            canasta.check_create_precondition(_args("local"))
        assert "host: localhost" in capsys.readouterr().err

    def test_fresh_id_returns_without_exit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        _seed_registry(str(tmp_path), {"other": {"path": "/opt/canasta/other"}})
        # Must not raise: a free ID falls through to the normal create path.
        assert canasta.check_create_precondition(_args("brandnew")) is None

    def test_no_registry_file_returns_without_exit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        assert canasta.check_create_precondition(_args("anything")) is None

    def test_missing_id_returns_without_exit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        _seed_registry(str(tmp_path), {"x": {"path": "/opt/canasta/x"}})
        assert canasta.check_create_precondition(_args(None)) is None
