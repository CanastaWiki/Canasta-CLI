"""Tests for backfilling the bundled-DB MYSQL_* defaults into .env.

podman-compose passes ${MYSQL_HOST:-db} through literally, so an instance
whose .env predates create pinning these keys hangs on start. Both the
Ansible start path and the Compose fast path must fill them in.
"""

import os
import sys

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import _helpers  # noqa: E402

HEAL = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "heal_mysql_defaults.yml")


def _inst(tmp_path):
    return {"path": str(tmp_path), "host": "localhost",
            "orchestrator": "compose"}


def _env(tmp_path):
    return (tmp_path / ".env").read_text()


class TestHelper:
    def test_missing_keys_are_added(self, tmp_path):
        (tmp_path / ".env").write_text("MYSQL_PASSWORD=pw\n")
        _helpers._backfill_db_defaults(_inst(tmp_path))
        assert _env(tmp_path) == (
            "MYSQL_PASSWORD=pw\nMYSQL_HOST=db\nMYSQL_PORT=3306\n"
            "MYSQL_USER=root\nMYSQL_SSL=false\n")

    def test_existing_values_are_kept(self, tmp_path):
        (tmp_path / ".env").write_text(
            "MYSQL_HOST=otherdb\nMYSQL_PORT=3307\nMYSQL_USER=wiki\n")
        _helpers._backfill_db_defaults(_inst(tmp_path))
        text = _env(tmp_path)
        assert "MYSQL_HOST=otherdb\n" in text
        assert "MYSQL_PORT=3307\n" in text
        assert "MYSQL_USER=wiki\n" in text
        assert "MYSQL_SSL=false\n" in text
        assert "MYSQL_HOST=db" not in text

    def test_empty_value_is_filled(self, tmp_path):
        (tmp_path / ".env").write_text(
            "MYSQL_HOST=\nMYSQL_PORT=3306\nMYSQL_USER=root\nMYSQL_SSL=false\n")
        _helpers._backfill_db_defaults(_inst(tmp_path))
        assert _env(tmp_path).startswith("MYSQL_HOST=db\n")

    def test_external_db_is_left_alone(self, tmp_path):
        content = "USE_EXTERNAL_DB=true\nMYSQL_HOST=db.example.com\n"
        (tmp_path / ".env").write_text(content)
        _helpers._backfill_db_defaults(_inst(tmp_path))
        assert _env(tmp_path) == content

    def test_complete_env_is_not_rewritten(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text(
            "MYSQL_HOST=db\nMYSQL_PORT=3306\nMYSQL_USER=root\nMYSQL_SSL=false\n")
        writes = []
        monkeypatch.setattr(_helpers, "_write_env_content",
                            lambda *a: writes.append(a))
        _helpers._backfill_db_defaults(_inst(tmp_path))
        assert writes == []

    def test_no_env_is_not_created(self, tmp_path):
        _helpers._backfill_db_defaults(_inst(tmp_path))
        assert not (tmp_path / ".env").exists()


class TestFastPathsBackfillBeforeUp:
    @pytest.fixture
    def calls(self, monkeypatch):
        seen = []
        monkeypatch.setattr(_helpers, "_resolve_instance",
                            lambda args: ("test", {"path": "/srv/test",
                                                   "orchestrator": "compose",
                                                   "host": "localhost"}))
        monkeypatch.setattr(_helpers, "_instance_has_sidecars",
                            lambda inst: False)
        monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                            lambda inst: {})
        monkeypatch.setattr(_helpers, "_check_running_compose",
                            lambda *a: False)
        monkeypatch.setattr(_helpers, "_missing_db_password",
                            lambda inst, env: False)
        monkeypatch.setattr(_helpers, "_wait_web_ready", lambda i, inst: 0)
        monkeypatch.setattr(_helpers, "_rootless_operator_chown",
                            lambda inst: None)
        monkeypatch.setattr(direct_commands.rebuild,
                            "_list_buildable_services",
                            lambda inst, include_sidecars=False: ["web"])
        monkeypatch.setattr(_helpers, "_backfill_db_defaults",
                            lambda inst: seen.append("backfill"))
        monkeypatch.setattr(_helpers, "_run_compose",
                            lambda i, inst, argv, **kw: seen.append(argv[0])
                            or 0)
        return seen

    @staticmethod
    def _args():
        return type("Args", (), {"id": "test", "no_cache": False,
                                 "no_restart": False})()

    @pytest.mark.parametrize("cmd", ["cmd_start", "cmd_restart",
                                     "cmd_rebuild"])
    def test_backfill_runs_before_up(self, calls, cmd):
        assert getattr(direct_commands, cmd)(self._args()) == 0
        assert "backfill" in calls
        assert calls.index("backfill") < calls.index("up")


class TestParityWithAnsible:
    def test_same_keys_and_values(self):
        with open(HEAL) as f:
            tasks = yaml.safe_load(f)
        block = next(t for t in tasks if "block" in t)["block"]
        ansible = [
            (t["canasta_env"]["key"], str(t["canasta_env"]["value"]))
            for t in block if t["canasta_env"].get("state") == "set"
        ]
        assert ansible == list(_helpers._BUNDLED_DB_DEFAULTS)
