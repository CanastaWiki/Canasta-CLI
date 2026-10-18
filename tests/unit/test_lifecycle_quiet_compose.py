"""The Compose fast path of start and stop reports like the Ansible path.

An instance on an external database takes the fast path (it has no
MediaWiki account on a bundled database to re-create), so its start and stop
must not stream Docker Compose's container lines and must end with the same
outcome line the Ansible path prints.
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import _helpers  # noqa: E402

COMPOSE_NOISE = " Container test-web-1  Started\n"
EXTERNAL_DB_ENV = {
    "USE_EXTERNAL_DB": "true",
    "MYSQL_HOST": "db.example.com",
    "WIKI_DB_USER": "root",
}


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Record compose invocations; answer with COMPOSE_NOISE and a set rc."""
    state = {"rc": 0, "run": [], "call": []}

    def fake_run(argv, **kw):
        state["run"].append((argv, kw))
        return subprocess.CompletedProcess(argv, state["rc"],
                                           stdout=COMPOSE_NOISE)

    def fake_call(argv, **kw):
        state["call"].append(argv)
        sys.stdout.write(COMPOSE_NOISE)
        return state["rc"]

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "call", fake_call)
    monkeypatch.setattr(_helpers, "_compose_file_args",
                        lambda *a, **kw: ["-f", "docker-compose.yml"])
    monkeypatch.setattr(_helpers, "_resolve_ssh_target", lambda h: h)
    return state


@pytest.fixture
def external_db_instance(monkeypatch, fake_subprocess):
    monkeypatch.setattr(_helpers, "_resolve_instance",
                        lambda args: ("test", {"path": "/srv/test",
                                               "orchestrator": "compose"}))
    monkeypatch.setattr(_helpers, "_instance_has_sidecars", lambda inst: False)
    monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                        lambda inst: dict(EXTERNAL_DB_ENV))
    monkeypatch.setattr(_helpers, "_backfill_db_defaults", lambda inst: None)
    monkeypatch.setattr(_helpers, "_crowdsec_setup_pending",
                        lambda inst, env: False)
    monkeypatch.setattr(_helpers, "_check_running_compose", lambda *a: False)
    monkeypatch.setattr(_helpers, "_missing_db_password",
                        lambda inst, env: False)
    monkeypatch.setattr(_helpers, "_wait_web_ready", lambda i, inst: 0)
    monkeypatch.setattr(_helpers, "_rootless_operator_chown",
                        lambda inst: None)
    monkeypatch.setattr(_helpers, "_warn_composer_failure",
                        lambda i, inst: None)
    return fake_subprocess


ARGS = type("Args", (), {"id": "test"})()


class TestStartStopOutput:

    def test_stop_is_quiet_and_reports_stopped(self, external_db_instance,
                                               capsys):
        assert direct_commands.cmd_stop(ARGS) == 0
        out, err = capsys.readouterr()
        assert "Container" not in out + err
        assert out.strip() == "Instance 'test' stopped."
        assert external_db_instance["call"] == []
        assert "down" in external_db_instance["run"][0][0]

    def test_failed_stop_shows_compose_output_and_no_outcome(
            self, external_db_instance, capsys):
        external_db_instance["rc"] = 1
        assert direct_commands.cmd_stop(ARGS) == 1
        out, err = capsys.readouterr()
        assert "stopped" not in out
        assert "Container test-web-1  Started" in err


BUNDLED_DB_ROOT_ENV = {"MYSQL_PASSWORD": "pw", "WIKI_DB_USER": "root"}
BUNDLED_DB_OWN_ACCOUNT_ENV = {"MYSQL_PASSWORD": "pw",
                              "WIKI_DB_USER": "mediawiki"}


class TestBundledDatabase:
    """Stop takes the fast path for every bundled-database instance; start
    does too while MediaWiki is on the root account."""

    @pytest.mark.parametrize("env", [BUNDLED_DB_ROOT_ENV,
                                     BUNDLED_DB_OWN_ACCOUNT_ENV])
    def test_stop_is_quiet_and_reports_stopped(
            self, external_db_instance, monkeypatch, capsys, env):
        monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                            lambda inst: dict(env))
        assert direct_commands.cmd_stop(ARGS) == 0
        out, err = capsys.readouterr()
        assert "Container" not in out + err
        assert out.strip() == "Instance 'test' stopped."

    def test_start_on_own_account_still_hands_over_to_ansible(
            self, external_db_instance, monkeypatch):
        monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                            lambda inst: dict(BUNDLED_DB_OWN_ACCOUNT_ENV))
        assert direct_commands.cmd_start(ARGS) == _helpers.FALLBACK
        assert external_db_instance["run"] == []


class TestRunComposeQuiet:
    @pytest.mark.parametrize("host", ["localhost", "admin@remote"])
    def test_quiet_captures_output(self, fake_subprocess, host, capsys):
        inst = {"path": "/srv/test", "orchestrator": "compose", "host": host}
        assert _helpers._run_compose("test", inst, ["down"], quiet=True) == 0
        out, err = capsys.readouterr()
        assert out == "" and err == ""
        kw = fake_subprocess["run"][0][1]
        assert kw["stdout"] == subprocess.PIPE
        assert kw["stderr"] == subprocess.STDOUT

    def test_default_still_streams(self, fake_subprocess, capsys):
        inst = {"path": "/srv/test", "orchestrator": "compose"}
        assert _helpers._run_compose("test", inst, ["build"]) == 0
        assert fake_subprocess["run"] == []
        assert fake_subprocess["call"]
        assert "Container" in capsys.readouterr().out
