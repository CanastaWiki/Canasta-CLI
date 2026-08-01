"""Tests for the Compose fast path's readiness gate.

`canasta start` used to return the moment `docker compose up -d` exited, while
the Ansible path waited for the web container to report healthy. Anything that
runs php in the container right after start — `canasta maintenance`,
install.php — then raced the image's synchronous `composer update` and crashed
on a half-written vendor/. These tests pin the fast path to the same contract
as roles/orchestrator/tasks/start.yml.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import _helpers  # noqa: E402


DOCKER = {"path": "/srv/test", "orchestrator": "compose"}
PODMAN = {"path": "/srv/test", "orchestrator": "compose",
          "composeCommand": "podman-compose", "inspectCommand": "podman"}


@pytest.fixture
def no_sleep(monkeypatch):
    """Record the waits instead of taking them."""
    slept = []
    monkeypatch.setattr(_helpers.time, "sleep", lambda s: slept.append(s))
    return slept


def _runtime(monkeypatch, responses):
    """Stub _runtime_capture with a queue of (rc, stdout) per call, replaying
    the last entry once exhausted. Returns the list of argv seen."""
    seen = []
    queue = list(responses)

    def fake(inst, argv, timeout=30):
        seen.append(argv)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(_helpers, "_runtime_capture", fake)
    return seen


class TestWaitWebReady:
    def test_returns_immediately_when_already_healthy(self, monkeypatch,
                                                      no_sleep):
        _runtime(monkeypatch, [(0, "abc123\n"), (0, "healthy\n")])
        assert _helpers._wait_web_ready("test", dict(DOCKER)) == 0
        assert no_sleep == []

    def test_waits_while_starting_then_passes(self, monkeypatch, no_sleep):
        _runtime(monkeypatch, [
            (0, "abc123\n"),
            (0, "starting\n"),
            (0, "starting\n"),
            (0, "healthy\n"),
        ])
        assert _helpers._wait_web_ready("test", dict(DOCKER)) == 0
        assert no_sleep == [_helpers._WEB_HEALTH_DELAY] * 2

    def test_fails_and_names_the_last_status_on_timeout(self, monkeypatch,
                                                        no_sleep, capsys):
        _runtime(monkeypatch, [(0, "abc123\n"), (0, "starting\n")])
        assert _helpers._wait_web_ready("test", dict(DOCKER)) == 1
        err = capsys.readouterr().err
        assert "did not report healthy" in err
        assert "'starting'" in err
        assert len(no_sleep) == _helpers._WEB_HEALTH_RETRIES

    def test_no_healthcheck_on_docker_is_not_waited_on(self, monkeypatch,
                                                       no_sleep):
        # A custom image that declares no HEALTHCHECK defines its own
        # readiness contract; waiting on a signal it never emits would hang.
        _runtime(monkeypatch, [(0, "abc123\n"), (0, "\n")])
        assert _helpers._wait_web_ready("test", dict(DOCKER)) == 0
        assert no_sleep == []

    def test_fails_when_no_web_container_is_running(self, monkeypatch, capsys):
        # `up -d` exiting 0 does not prove the stack is up: with rootless
        # podman and no lingering, systemd kills the containers as the ssh
        # session ends. Without this the gate would just be skipped.
        _runtime(monkeypatch, [(0, "\n")])
        assert _helpers._wait_web_ready("test", dict(DOCKER)) == 1
        err = capsys.readouterr().err
        assert "no running 'web' container" in err
        assert "enable-linger" in err

    def test_probes_server_status_on_podman(self, monkeypatch, no_sleep):
        # Podman renders an empty health status for the stock (OCI) image, so
        # ask the container the question the healthcheck would ask.
        seen = _runtime(monkeypatch, [
            (0, "abc123\n"),
            (0, "\n"),
            (1, ""),
            (0, ""),
        ])
        assert _helpers._wait_web_ready("test", dict(PODMAN)) == 0
        probe = seen[-1]
        assert probe[:3] == ["podman", "exec", "abc123"]
        assert "127.0.0.1/server-status" in probe

    def test_podman_probe_is_best_effort(self, monkeypatch, no_sleep, capsys):
        # An image that never serves /server-status must not turn a missing
        # signal into a failed start.
        _runtime(monkeypatch, [(0, "abc123\n"), (0, "\n"), (1, "")])
        assert _helpers._wait_web_ready("test", dict(PODMAN)) == 0
        assert "did not answer /server-status" in capsys.readouterr().err

    def test_container_lookup_is_scoped_to_the_instance_project(
            self, monkeypatch, no_sleep):
        seen = _runtime(monkeypatch, [(0, "abc123\n"), (0, "healthy\n")])
        _helpers._wait_web_ready("test", dict(DOCKER))
        ps = seen[0]
        assert "label=com.docker.compose.project=test" in ps
        assert "label=com.docker.compose.service=web" in ps


class TestRuntimeCapture:
    def test_remote_probe_goes_over_ssh(self, monkeypatch):
        calls = []

        def fake_ssh(host, cmd):
            calls.append((host, cmd))
            return 0, "healthy\n"

        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        rc, out = _helpers._runtime_capture(
            dict(DOCKER, host="admin@remote"), ["docker", "ps"])
        assert (rc, out.strip()) == (0, "healthy")
        assert calls[0][0] == "admin@remote"
        assert "docker" in calls[0][1]

    def test_remote_probe_carries_the_instances_docker_host(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            _helpers, "_ssh_run",
            lambda host, cmd: (calls.append(cmd), (0, ""))[1])
        _helpers._runtime_capture(
            dict(DOCKER, host="admin@remote",
                 dockerHost="unix:///run/user/1000/docker.sock"),
            ["docker", "ps"])
        assert calls[0].startswith("DOCKER_HOST=")


class TestLifecycleGating:
    """start/restart return the gate's verdict, and only after a clean `up`."""

    def _inst(self, monkeypatch, events):
        monkeypatch.setattr(
            _helpers, "_resolve_instance", lambda args: ("test", dict(DOCKER)))
        monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                            lambda inst: None)
        monkeypatch.setattr(
            _helpers, "_run_compose",
            lambda inst_id, inst, action: events.append(action[0]) or 0)
        return type("Args", (), {"id": "test"})()

    def test_start_returns_the_gates_failure(self, monkeypatch):
        events = []
        args = self._inst(monkeypatch, events)
        monkeypatch.setattr(_helpers, "_wait_web_ready",
                            lambda i, inst: events.append("wait") or 1)
        assert direct_commands.cmd_start(args) == 1
        assert events == ["up", "wait"]

    def test_start_does_not_wait_when_up_failed(self, monkeypatch):
        events = []
        monkeypatch.setattr(
            _helpers, "_resolve_instance", lambda args: ("test", dict(DOCKER)))
        monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                            lambda inst: None)
        monkeypatch.setattr(_helpers, "_run_compose",
                            lambda inst_id, inst, action: 1)
        monkeypatch.setattr(_helpers, "_dump_compose_failure",
                            lambda inst, **kw: events.append("dump"))
        monkeypatch.setattr(_helpers, "_wait_web_ready",
                            lambda i, inst: events.append("wait") or 0)
        args = type("Args", (), {"id": "test"})()
        assert direct_commands.cmd_start(args) == 1
        assert events == ["dump"]

    def test_restart_waits_too(self, monkeypatch):
        # The Ansible restart path includes start.yml, gate and all.
        events = []
        args = self._inst(monkeypatch, events)
        monkeypatch.setattr(_helpers, "_wait_web_ready",
                            lambda i, inst: events.append("wait") or 0)
        assert direct_commands.cmd_restart(args) == 0
        assert events == ["down", "up", "wait"]
