"""Under canasta-docker, a local doctor runs inside the CLI container.

The container is not the host: its groups, crontab, sudo and lack of a
native install say nothing about the host the operator is checking. A
local doctor in docker mode therefore skips those checks, and reports
the host's canasta as the wrapper that launched it. A remote target is
probed over SSH, so it keeps every check.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from direct_commands import doctor  # noqa: E402
from direct_commands import _helpers  # noqa: E402


D = _helpers._SENTINEL

# What the probes return inside the CLI container on a Linux host: the
# container user has only the socket and www-data groups, no crontab, no
# canasta on PATH, and no sudo.
_CONTAINER_FIELDS = [
    "Python 3.12.0", "Docker version 27.0.0",
    "Docker Compose version v2.30.0", "OK",
    "user www-data", "OK", "v3.15.0", "MISSING",
    "UNREACHABLE", "MISSING", "git version 2.45.0", "OK", "MISSING",
    "Linux", "16 GB", "50G", "0", "", "MISSING", "NOT_WRITABLE",
    "OK", "OK", "MISSING", "MISSING", "MISSING", "MISSING", "",
]
_STDOUT = (D + "\n").join(_CONTAINER_FIELDS) + "\n"


def _run_doctor(monkeypatch, capsys, host=None):
    monkeypatch.setattr(_helpers, "_get_config_dir", lambda: "/nonexistent")
    monkeypatch.setattr(_helpers, "_read_registry", lambda path: {})
    monkeypatch.setattr(
        _helpers, "_resolve_instance_by_cwd", lambda args: (None, None))
    monkeypatch.setattr(doctor, "_collection_integrity_lines", lambda: [])
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, _STDOUT, ""))
    monkeypatch.setattr(_helpers, "_ssh_run", lambda h, s, **k: (0, _STDOUT))
    args = type("Args", (), {"host": host, "id": None})()
    rc = doctor.cmd_doctor(args)
    assert rc == 0
    return capsys.readouterr().out


class TestLocalDockerMode:
    def _out(self, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        monkeypatch.setenv("CANASTA_CLI_BIN", "/usr/local/bin/canasta")
        return _run_doctor(monkeypatch, capsys)

    def test_group_checks_are_skipped(self, monkeypatch, capsys):
        out = self._out(monkeypatch, capsys)
        assert "canasta group" not in out
        assert "www-data group" not in out
        assert "NOT A MEMBER" not in out

    def test_native_self_update_advice_is_dropped(self, monkeypatch, capsys):
        out = self._out(monkeypatch, capsys)
        assert "Self-update" not in out
        assert "self-updates" not in out

    def test_crontab_is_not_reported_missing(self, monkeypatch, capsys):
        out = self._out(monkeypatch, capsys)
        assert "crontab:" not in out

    def test_one_line_says_host_checks_are_skipped(
            self, monkeypatch, capsys):
        out = self._out(monkeypatch, capsys)
        skipped = [ln for ln in out.splitlines() if "docker mode" in ln]
        assert len(skipped) == 1
        assert "SKIPPED" in skipped[0]

    def test_host_canasta_is_the_wrapper(self, monkeypatch, capsys):
        out = self._out(monkeypatch, capsys)
        assert "canasta on host: OK (/usr/local/bin/canasta)" in out
        assert "canasta on host: not installed" not in out

    def test_resource_checks_still_run(self, monkeypatch, capsys):
        out = self._out(monkeypatch, capsys)
        assert "Memory:          16 GB" in out
        assert "Docker daemon:   OK (running)" in out


class TestUnaffectedPaths:
    def test_native_mode_keeps_host_checks(self, monkeypatch, capsys):
        monkeypatch.delenv("CANASTA_RUN_MODE", raising=False)
        out = _run_doctor(monkeypatch, capsys)
        assert "canasta group:   NOT A MEMBER" in out
        assert "Self-update:     BLOCKED" in out
        assert "crontab:         not installed" in out
        assert "canasta on host: not installed" in out
        assert "docker mode" not in out

    def test_remote_host_in_docker_mode_keeps_host_checks(
            self, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        out = _run_doctor(monkeypatch, capsys, host="prod1.example.com")
        assert "Canasta Dependency Check (prod1.example.com)" in out
        assert "canasta group:   NOT A MEMBER" in out
        assert "crontab:         not installed" in out
        assert "docker mode" not in out
