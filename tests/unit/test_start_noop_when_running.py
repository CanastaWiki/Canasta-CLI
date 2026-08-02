"""Starting a running instance must be a no-op, and must be seen to be one.

podman-compose's `up -d` fails outright when the containers already
exist:

    Error: creating container storage: the container name
    "nichework_db_1" is already in use by 786c436060...

Docker Compose treats that as a no-op. cmd_start now asks whether the
instance is already running and skips the compose call when it is.

The branch that introduced this added no test for it — the
_check_running_compose stubs it added were to keep *existing* tests
passing. So the headline behavior, and the bug it fixes, had no coverage.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import direct_commands  # noqa: E402
import direct_commands._helpers  # noqa: E402


def _instance():
    return ("test", {"path": "/srv/test", "orchestrator": "compose"})


def _args():
    return type("Args", (), {"id": "test"})()


def _common(monkeypatch, calls):
    monkeypatch.setattr(subprocess, "call",
                        lambda cmd, **kw: calls.append(cmd) or 0)
    monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
                        lambda args: _instance())
    monkeypatch.setattr(direct_commands._helpers, "_compose_file_args",
                        lambda *a, **kw: ["-f", "docker-compose.yml"])
    monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
                        lambda inst: None)
    monkeypatch.setattr(direct_commands._helpers, "_wait_web_ready",
                        lambda i, inst: 0)


class TestARunningInstanceIsNotStartedAgain:
    def test_compose_is_not_invoked(self, monkeypatch):
        calls = []
        _common(monkeypatch, calls)
        monkeypatch.setattr(direct_commands._helpers,
                            "_check_running_compose", lambda *a, **kw: True)

        assert direct_commands.cmd_start(_args()) == 0
        assert calls == [], (
            "compose ran against an already-running instance, which is the "
            "podman-compose name-conflict failure this exists to avoid"
        )

    def test_it_says_so(self, monkeypatch, capsys):
        calls = []
        _common(monkeypatch, calls)
        monkeypatch.setattr(direct_commands._helpers,
                            "_check_running_compose", lambda *a, **kw: True)

        direct_commands.cmd_start(_args())
        assert "already running" in capsys.readouterr().out

    def test_it_succeeds(self, monkeypatch):
        # A no-op is success, not a silent failure: scripts chain off this.
        calls = []
        _common(monkeypatch, calls)
        monkeypatch.setattr(direct_commands._helpers,
                            "_check_running_compose", lambda *a, **kw: True)

        assert direct_commands.cmd_start(_args()) == 0


class TestAStoppedInstanceStillStarts:
    def test_compose_is_invoked(self, monkeypatch):
        # The mirror image, and the reason the check is running-only: a
        # stopped instance whose containers still exist must still start.
        calls = []
        _common(monkeypatch, calls)
        monkeypatch.setattr(direct_commands._helpers,
                            "_check_running_compose", lambda *a, **kw: False)

        assert direct_commands.cmd_start(_args()) == 0
        assert calls, "a stopped instance was skipped and left down"
        assert calls[0][-2:] == ["up", "-d"]


class TestReadinessStillGatesTheStart:
    def test_wait_web_ready_is_called_after_a_real_start(self, monkeypatch):
        calls = []
        _common(monkeypatch, calls)
        waited = []
        monkeypatch.setattr(direct_commands._helpers, "_wait_web_ready",
                            lambda i, inst: waited.append(i) or 0)
        monkeypatch.setattr(direct_commands._helpers,
                            "_check_running_compose", lambda *a, **kw: False)

        direct_commands.cmd_start(_args())
        assert waited == ["test"], (
            "the readiness gate from #1382 was skipped; canasta start "
            "would return before the wiki can serve"
        )
