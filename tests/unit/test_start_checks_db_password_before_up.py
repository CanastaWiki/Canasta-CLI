"""A missing MYSQL_PASSWORD has to be caught before the containers start.

The heal in orchestrator/tasks/heal_mysql_password.yml only runs on the
Ansible path, and cmd_start reached it by returning FALLBACK when
`compose up` failed. That premise was wrong: MariaDB applies the root
password only when it initialises an empty data directory, so on an
instance whose volume already holds a database, compose comes up clean
with the variable empty. Every container reports healthy and only
MediaWiki notices it cannot authenticate.

Observed end to end: `canasta start` printed the containers, ran the
readiness gate, exited 0 — and the wiki served HTTP 500 "Cannot access
the database". The heal never ran, because `up` never failed.

So the check runs before `up`. The read costs nothing: cmd_start already
calls _sync_compose_profiles, which parses .env one line earlier and now
hands it back.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import direct_commands  # noqa: E402
import direct_commands._helpers  # noqa: E402


def _args():
    return type("Args", (), {"id": "test"})()


def _common(monkeypatch, calls, env):
    monkeypatch.setattr(subprocess, "call",
                        lambda cmd, **kw: calls.append(cmd) or 0)
    monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
                        lambda args: ("test", {"path": "/srv/test",
                                               "orchestrator": "compose"}))
    monkeypatch.setattr(direct_commands._helpers, "_compose_file_args",
                        lambda *a, **kw: ["-f", "docker-compose.yml"])
    monkeypatch.setattr(direct_commands._helpers, "_check_running_compose",
                        lambda *a, **kw: False)
    monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
                        lambda inst: env)
    monkeypatch.setattr(direct_commands._helpers, "_wait_web_ready",
                        lambda i, inst: 0)


class TestTheCheckRunsBeforeAnythingStarts:
    def test_a_missing_password_hands_over_without_starting(self, monkeypatch):
        calls = []
        _common(monkeypatch, calls, {"MYSQL_PASSWORD": "", "OTHER": "x"})

        assert direct_commands.cmd_start(_args()) == direct_commands._helpers.FALLBACK
        assert calls == [], (
            "compose started the stack before the password was checked; the "
            "wiki comes up unable to authenticate and start reports success"
        )

    def test_an_absent_key_counts_as_missing(self, monkeypatch):
        calls = []
        _common(monkeypatch, calls, {"OTHER": "x"})

        assert direct_commands.cmd_start(_args()) == direct_commands._helpers.FALLBACK
        assert calls == []

    def test_whitespace_only_counts_as_missing(self, monkeypatch):
        calls = []
        _common(monkeypatch, calls, {"MYSQL_PASSWORD": "   ", "OTHER": "x"})

        assert direct_commands.cmd_start(_args()) == direct_commands._helpers.FALLBACK
        assert calls == []


class TestAHealthyInstanceIsUnaffected:
    def test_a_present_password_starts_normally(self, monkeypatch):
        calls = []
        _common(monkeypatch, calls, {"MYSQL_PASSWORD": "s3cret", "OTHER": "x"})

        assert direct_commands.cmd_start(_args()) == 0
        assert calls, "a healthy instance was not started"
        assert calls[0][-2:] == ["up", "-d"]

    def test_an_unreadable_env_is_not_treated_as_this_problem(self, monkeypatch):
        # An empty parse means no .env or an unreadable one — a different
        # fault. Claiming it as a missing password would send every such
        # instance down the heal path instead of reporting what is wrong.
        calls = []
        _common(monkeypatch, calls, {})

        assert direct_commands.cmd_start(_args()) == 0
        assert calls, "an unreadable .env was misread as a missing password"


class TestTheEnvIsReadOnlyOnce:
    def test_a_supplied_env_is_not_re_read(self, monkeypatch):
        reads = []
        monkeypatch.setattr(direct_commands._helpers, "_read_env_file",
                            lambda p, h: reads.append(p) or {})

        assert direct_commands._helpers._missing_db_password(
            {"path": "/srv/test"}, {"MYSQL_PASSWORD": "", "OTHER": "x"}) is True
        assert reads == [], (
            "a second .env read is a second SSH round trip on a remote "
            "instance, on every start"
        )

    def test_it_still_reads_when_no_env_is_supplied(self, monkeypatch):
        reads = []
        monkeypatch.setattr(
            direct_commands._helpers, "_read_env_file",
            lambda p, h: reads.append(p) or {"MYSQL_PASSWORD": "", "K": "v"})

        assert direct_commands._helpers._missing_db_password(
            {"path": "/srv/test"}) is True
        assert reads == ["/srv/test"]


class TestSyncComposeProfilesHandsBackWhatItParsed:
    def _inst(self, tmp_path):
        return {"path": str(tmp_path), "host": "localhost"}

    def test_it_returns_the_parsed_env_when_nothing_changed(self, tmp_path):
        (tmp_path / ".env").write_text(
            "COMPOSE_PROFILES=varnish\nMYSQL_PASSWORD=s3cret\n")
        env = direct_commands._sync_compose_profiles(self._inst(tmp_path))
        assert env["MYSQL_PASSWORD"] == "s3cret"

    def test_it_returns_the_parsed_env_when_it_rewrote_profiles(self, tmp_path):
        (tmp_path / ".env").write_text(
            "CANASTA_ENABLE_ELASTICSEARCH=true\nCOMPOSE_PROFILES=\n"
            "MYSQL_PASSWORD=s3cret\n")
        env = direct_commands._sync_compose_profiles(self._inst(tmp_path))
        assert "COMPOSE_PROFILES=elasticsearch" in (
            tmp_path / ".env").read_text()
        assert env["MYSQL_PASSWORD"] == "s3cret"

    def test_no_env_file_yields_an_empty_mapping(self, tmp_path):
        # Falsy, so the caller treats it as "nothing to judge on" rather
        # than as a missing password.
        assert direct_commands._sync_compose_profiles(
            self._inst(tmp_path)) == {}
