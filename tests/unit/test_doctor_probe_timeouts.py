"""A slow probe command reports unknown, and a slow probe is a failed check.

On a busy host `docker info` and `docker system df` can stall. Each gets a
short cap of its own, and a probe that still outruns its overall limit is
reported rather than ending doctor before its other checks.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from direct_commands import doctor  # noqa: E402
from direct_commands import _helpers  # noqa: E402

D = _helpers._SENTINEL

# timeout(1) is not on every host the tests run on, so a stand-in with
# its exit code.
_FAKE_TIMEOUT = """#!%s
import subprocess, sys
try:
    sys.exit(subprocess.run(sys.argv[2:], timeout=float(sys.argv[1])).returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
""" % sys.executable

# A Podman alongside, which must not stand in for the stalled Docker.
_PODMAN = """#!/bin/sh
case "$1" in
  info) echo /var/lib/podman-store ;;
  system) echo "Images|9GB (90%)" ;;
esac
"""

_SLOW_DOCKER = """#!/bin/sh
case "$1" in
  --version) echo "Docker version 27.0.0" ;;
  compose) echo "Docker Compose version v2.30.0" ;;
  info|system) exec sleep 5 ;;
esac
"""


def _probe(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("timeout", _FAKE_TIMEOUT), ("docker", _SLOW_DOCKER),
                       ("podman", _PODMAN)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setattr(doctor, "PROBE_COMMAND_TIMEOUT", 1)
    script = doctor._DOCTOR_SCRIPT % {"delim": D, "cmd_timeout": 1}
    env = {"PATH": "%s:/usr/bin:/bin" % bin_dir, "HOME": str(tmp_path)}
    started = time.time()
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         env=env, timeout=60).stdout
    return out, time.time() - started


def test_a_stalled_docker_reports_unknown_rather_than_not_running(tmp_path, monkeypatch):
    out, elapsed = _probe(tmp_path, monkeypatch)
    probes = doctor._doctor_probes(out)
    assert probes["daemon"] == "TIMEOUT"
    assert probes["system_df"] == "unknown"
    assert not probes["disk"].endswith("|/var/lib/podman-store")
    # docker info twice and docker system df, each cut off at 1 second.
    assert elapsed < 10
    report = doctor._parse_doctor(out, "h")
    assert "Docker daemon:   unknown ('docker info' took over 1 seconds)" in report
    assert "NOT RUNNING" not in report
    assert doctor._has_container_runtime(
        probes["docker"], probes["compose"], probes["daemon"], probes["podman_version"])


def _run_doctor(monkeypatch, capsys, run):
    monkeypatch.setattr(_helpers, "_get_config_dir", lambda: "/nonexistent")
    monkeypatch.setattr(_helpers, "_read_registry", lambda path: {})
    monkeypatch.setattr(
        _helpers, "_resolve_instance_by_cwd", lambda args: (None, None))
    monkeypatch.setattr(doctor, "_collection_integrity_lines", lambda: ["  collections: OK"])
    monkeypatch.setattr(subprocess, "run", run)
    args = type("Args", (), {"host": None, "id": None})()
    rc = doctor.cmd_doctor(args)
    return rc, capsys.readouterr().out


def test_a_probe_that_outruns_its_limit_is_a_failed_check(monkeypatch, capsys):
    partial = ("Python 3.12.0\n" + D + "\nDocker version 27.0.0\n" + D + "\n").encode()

    def run(argv, **kwargs):
        assert kwargs["timeout"] == doctor.PROBE_TIMEOUT
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=partial)

    rc, out = _run_doctor(monkeypatch, capsys, run)
    assert rc == 1
    assert "Docker:          OK (Docker version 27.0.0)" in out
    assert "Host checks: FAIL — they did not finish within 120 seconds" in out
    # The checks after the probe still run.
    assert "collections: OK" in out


def test_a_remote_probe_gets_the_same_limit(monkeypatch, capsys):
    seen = []

    def ssh_run(host, script, timeout=30):
        seen.append(timeout)
        return 255, ""

    monkeypatch.setattr(_helpers, "_ssh_run", ssh_run)
    monkeypatch.setattr(_helpers, "_get_config_dir", lambda: "/nonexistent")
    monkeypatch.setattr(_helpers, "_read_registry", lambda path: {})
    monkeypatch.setattr(
        _helpers, "_resolve_instance_by_cwd", lambda args: (None, None))
    args = type("Args", (), {"host": "prod1.example.com", "id": None})()
    assert doctor.cmd_doctor(args) == 1
    assert seen == [doctor.PROBE_TIMEOUT]
