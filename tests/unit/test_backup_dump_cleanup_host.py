"""Staged database dumps are removed on the host, web container or not.

backup_cleanup_db_dumps.yml removes instance_path/config/backup from the
host. Only a directory the host user cannot remove goes through the web
container, and one that survives both is reported rather than ignored.
"""

import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
CLEANUP = os.path.join(TASKS, "backup_cleanup_db_dumps.yml")
RESTORE_INSTANCE = os.path.join(TASKS, "restore_instance.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _instance(tmp_path):
    inst = tmp_path / "inst"
    (inst / "config" / "backup").mkdir(parents=True)
    (inst / "config" / "backup" / "db_main.sql").write_text("-- dump\n")
    return inst


def _run_cleanup(tmp_path, inst, compose_command):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "instance_orchestrator": "compose", **INSTANCE_IS_K8S_VARS,
                 "compose_command": compose_command,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": CLEANUP}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


@pytest.fixture
def locked_config(tmp_path):
    """An instance whose config/ the host user cannot write, as when the
    web container has re-owned it."""
    if os.geteuid() == 0:
        pytest.skip("root can remove anything")
    inst = _instance(tmp_path)
    os.chmod(inst / "config", 0o555)
    yield inst
    os.chmod(inst / "config", 0o755)


def test_removes_dumps_on_the_host_without_the_container(tmp_path):
    inst = _instance(tmp_path)
    # A compose command that fails proves the container is never needed.
    proc = _run_cleanup(tmp_path, inst, "false")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (inst / "config" / "backup").exists()
    assert "WARNING" not in proc.stdout


def test_falls_back_to_the_web_container(tmp_path, locked_config):
    # Stands in for `compose exec web rm -rf ...`, which runs as the
    # container's root and so is not stopped by config/'s permissions.
    fake_exec = "chmod u+w config && rm -rf config/backup && echo"
    proc = _run_cleanup(tmp_path, locked_config, fake_exec)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (locked_config / "config" / "backup").exists()
    assert "WARNING" not in proc.stdout


def test_warns_when_the_dumps_survive(tmp_path, locked_config):
    proc = _run_cleanup(tmp_path, locked_config, "false")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (locked_config / "config" / "backup").exists()
    assert "WARNING: could not remove" in proc.stdout


def test_restore_uses_the_shared_cleanup():
    with open(RESTORE_INSTANCE) as f:
        text = f.read()
    assert "rm -rf /mediawiki/config/backup" not in text
    tasks = yaml.safe_load(text)

    def walk(ts):
        for t in ts or []:
            if isinstance(t, dict):
                yield t
                for k in ("block", "rescue", "always"):
                    yield from walk(t.get(k))

    by_name = {t.get("name"): t for t in walk(tasks)}
    for name in ("Clean up safety DB dump directory",
                 "Clean up backup dump directory"):
        assert (by_name[name].get("ansible.builtin.include_tasks")
                == "backup_cleanup_db_dumps.yml")
