"""restore, delete, start, stop and restart hold a per-instance lock.

Two of them used to run against one instance at once: a second restore read
the first one's live marker as an interrupted restore and ran over it, and a
delete went ahead mid-restore. canasta.py now takes an flock on
<config dir>/locks/<id>.lock before running the command. The descriptor is
inheritable, so the lock survives the exec into ansible-playbook and is
released by the kernel when that process ends, however it ends.

These run the real function against a temporary registry, with a separate
process holding the lock across an exec.
"""

import argparse
import json
import os
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402

HOLDER = r"""
import os, sys, argparse
sys.path.insert(0, %(root)r)
import canasta
canasta.acquire_instance_lock("backup_restore", argparse.Namespace(id="demo"))
sys.stdout.write("locked\n")
sys.stdout.flush()
os.execvp("sleep", ["sleep", "30"])
"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    inst = tmp_path / "inst"
    cfg.mkdir()
    inst.mkdir()
    (cfg / "conf.json").write_text(json.dumps({
        "Instances": {"demo": {"path": str(inst), "orchestrator": "compose",
                               "host": "localhost"}},
    }))
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CANASTA_HOST_NAME", "controller1")
    return cfg


@pytest.fixture
def holder(registry):
    """A process that took the lock and then exec'd into `sleep`."""
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER % {"root": REPO_ROOT}],
        stdout=subprocess.PIPE, env=os.environ.copy())
    assert proc.stdout.readline() == b"locked\n"
    yield proc
    proc.kill()
    proc.wait()


def _acquire(command):
    return canasta.acquire_instance_lock(command, argparse.Namespace(id="demo"))


def test_a_second_locked_command_is_refused(holder, capsys):
    for command in ("backup_restore", "delete", "start", "stop", "restart"):
        with pytest.raises(SystemExit) as exc:
            _acquire(command)
        assert exc.value.code == canasta.EXIT_INSTANCE_BUSY
        err = capsys.readouterr().err
        assert "a restore of instance 'demo' is in progress" in err
        assert "on controller1" in err


def test_the_lock_is_released_when_the_holder_exits(holder):
    holder.kill()
    holder.wait()
    fd = _acquire("delete")
    assert fd is not None
    os.close(fd)


def test_unlocked_commands_do_not_take_the_lock(holder):
    assert _acquire("backup_create") is None
    assert _acquire("config_set") is None


def test_the_lock_records_the_command_and_survives_exec(registry):
    fd = _acquire("stop")
    try:
        assert os.get_inheritable(fd)
        record = json.loads(
            (registry / "locks" / "demo.lock").read_text())
        assert record["command"] == "stop"
        assert record["host"] == "controller1"
        assert time.strptime(record["started"], "%Y-%m-%dT%H:%M:%SZ")
    finally:
        os.close(fd)


def test_an_unknown_instance_is_left_to_the_command(registry):
    assert canasta.acquire_instance_lock(
        "delete", argparse.Namespace(id="nope")) is None


def test_main_takes_the_lock_before_either_path():
    with open(os.path.join(REPO_ROOT, "canasta.py")) as f:
        src = f.read()
    main = src[src.index("def main():"):]
    assert main.index("acquire_instance_lock(command_name, args)") < \
        main.index("direct_commands.run_direct_command")
