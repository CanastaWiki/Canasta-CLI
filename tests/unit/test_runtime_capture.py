"""Tests for _runtime_capture, which runs a container-runtime command for an
instance on its own host."""

import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers


DOCKER = {"path": "/srv/test", "orchestrator": "compose"}


class TestRuntimeCapture:
    def test_remote_probe_goes_over_ssh(self, monkeypatch):
        calls = []

        def fake_ssh(host, cmd, docker_host=None, timeout=30):
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
            lambda host, cmd, docker_host=None, timeout=30:
                (calls.append(docker_host), (0, ""))[1])
        _helpers._runtime_capture(
            dict(DOCKER, host="admin@remote",
                 dockerHost="unix:///run/user/1000/docker.sock"),
            ["docker", "ps"])
        assert calls[0] == "unix:///run/user/1000/docker.sock"

    def test_remote_probe_keeps_the_callers_timeout(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            _helpers, "_ssh_run",
            lambda host, cmd, docker_host=None, timeout=30:
                (calls.append(timeout), (0, ""))[1])
        _helpers._runtime_capture(
            dict(DOCKER, host="admin@remote"), ["podman", "rm", "-f", "x"],
            timeout=120)
        assert calls == [120]
