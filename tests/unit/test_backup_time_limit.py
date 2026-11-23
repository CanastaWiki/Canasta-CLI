"""How long a Compose database dump or import may run.

CANASTA_BACKUP_TIMEOUT in .env sets it in minutes, 240 by default. A dump
or import still running at the limit fails with how to allow more time.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
TIME_LIMIT = os.path.join(TASKS, "backup_time_limit.yml")
RESILIENT = os.path.join(TASKS, "resilient_exec.yml")


def _play(tmp_path, tasks, play_vars=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": dict({"ansible_python_interpreter": sys.executable},
                     **(play_vars or {})),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="default",
               ANSIBLE_ASYNC_DIR=str(tmp_path / "async"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


def _limit(tmp_path, env_text):
    (tmp_path / ".env").write_text(env_text)
    return _play(tmp_path, [
        {"ansible.builtin.include_tasks": TIME_LIMIT},
        {"ansible.builtin.debug": {
            "msg": "LIMIT={{ _backup_time_limit }} HINT={{ _backup_time_limit_hint }}"}},
    ], {"instance_path": str(tmp_path), "instance_id": "mysite"})


HINT = ("To allow more time, run: canasta config set "
        "CANASTA_BACKUP_TIMEOUT=<minutes> -i mysite --no-restart")


@pytest.mark.parametrize("env_text, seconds", [
    ("", 14400),
    ("CANASTA_BACKUP_TIMEOUT=\n", 14400),
    ("CANASTA_BACKUP_TIMEOUT=90\n", 5400),
    ("CANASTA_BACKUP_TIMEOUT=1\n", 60),
])
def test_the_limit_comes_from_env_in_minutes(tmp_path, env_text, seconds):
    rc, out = _limit(tmp_path, env_text)
    assert rc == 0, out
    assert "LIMIT=%d HINT=%s" % (seconds, HINT) in out


@pytest.mark.parametrize("value", ["0", "abc", "1.5", "-5", "60m"])
def test_a_limit_that_is_not_whole_minutes_is_refused(tmp_path, value):
    rc, out = _limit(tmp_path, "CANASTA_BACKUP_TIMEOUT=%s\n" % value)
    assert rc != 0, out
    assert ("CANASTA_BACKUP_TIMEOUT must be a whole number of minutes greater "
            "than 0, not '%s'. Nothing was changed." % value) in out
    assert "LIMIT=" not in out


def _exec(tmp_path, **rx_vars):
    rx = {"rx_name": "dump main", "rx_delay": 1, "rx_timeout_hint": HINT}
    rx.update(rx_vars)
    return _play(tmp_path, [{"ansible.builtin.include_tasks": RESILIENT,
                             "vars": rx}])


def test_a_dump_that_runs_out_of_time_says_how_to_allow_more(tmp_path):
    rc, out = _exec(tmp_path, rx_cmd="sleep 30", rx_async=2, rx_retries=20)
    assert rc != 0, out
    assert ("dump main did not finish within 1 minute and may still be "
            "running. " + HINT) in out


def test_a_dump_that_fails_does_not_mention_the_limit(tmp_path):
    rc, out = _exec(tmp_path, rx_cmd="echo boom >&2; exit 3", rx_async=60,
                    rx_retries=30)
    assert rc != 0, out
    assert "dump main failed (rc=3): boom" in out
    assert "CANASTA_BACKUP_TIMEOUT" not in out


# --- where it applies ----------------------------------------------------- #


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _tasks(name):
    with open(os.path.join(TASKS, name)) as handle:
        return list(_walk(yaml.safe_load(handle)))


def _uses_the_limit(task):
    rx = task.get("vars") or {}
    return (rx.get("rx_async") == "{{ _backup_time_limit }}"
            and rx.get("rx_retries") == "{{ ((_backup_time_limit | int) / 10) | int }}"
            and rx.get("rx_timeout_hint") == "{{ _backup_time_limit_hint }}")


def test_each_dump_uses_the_limit():
    (dump,) = [t for t in _tasks("backup_dump_db_group.yml")
               if t.get("ansible.builtin.include_tasks") == "resilient_exec.yml"]
    assert _uses_the_limit(dump)
    stage = _tasks("backup_stage_db_dumps.yml")
    includes = [t.get("ansible.builtin.include_tasks") for t in stage]
    assert (includes.index("backup_time_limit.yml")
            < includes.index("backup_dump_db_group.yml"))


def test_each_restore_import_uses_the_limit():
    tasks = _tasks("restore_instance.yml")
    (imp,) = [t for t in tasks if t.get("name") == "Import each wiki database dump"]
    assert _uses_the_limit(imp)
    names = [t.get("name") for t in tasks]
    limit = [i for i, t in enumerate(tasks)
             if t.get("ansible.builtin.include_tasks") == "backup_time_limit.yml"]
    assert limit and limit[0] < names.index("Import each wiki database dump")
