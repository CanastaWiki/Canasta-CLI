"""config set --no-restart tells a stopped instance's operator to start it.

`canasta reconcile` applies changes to a running instance; on a stopped
one, the changes take effect when it starts, so that is what the final
message says.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SET = os.path.join(REPO_ROOT, "roles", "config", "tasks", "set.yml")


def _tasks():
    with open(SET) as f:
        return yaml.safe_load(f)


def _index(tasks, name):
    for i, t in enumerate(tasks):
        if t.get("name") == name:
            return i
    raise AssertionError(f"task {name!r} not found")


def _run(tmp_path, **play_vars):
    tasks = _tasks()
    report = tasks[_index(tasks, "Report settings applied")]
    out = tmp_path / "msg.txt"
    play = tmp_path / "play.yml"
    play_vars.setdefault("instance_id", "mysite")
    play_vars.setdefault("_config_set_stage_only", False)
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": play_vars,
        "tasks": [
            dict(report, register="_report"),
            {"ansible.builtin.copy": {"content": "{{ _report.msg }}",
                                      "dest": str(out)}},
        ],
    }]))
    collections = tmp_path / "collections"
    collections.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_COLLECTIONS_PATH=str(collections),
               ANSIBLE_HOME=str(tmp_path / "ansible-home"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out.read_text()


class TestNoRestartMessage:
    def test_stopped_instance_is_told_to_start(self, tmp_path):
        msg = _run(tmp_path, no_restart=True, _container_running=False)
        assert "reconcile" not in msg
        assert "running instance" not in msg
        assert "take effect when it starts" in msg
        assert "`canasta start -i mysite`" in msg

    def test_running_instance_is_told_to_reconcile(self, tmp_path):
        msg = _run(tmp_path, no_restart=True, _container_running=True)
        assert msg == (
            "Settings updated. Restart skipped — run `canasta reconcile` "
            "to apply the changes to the running instance.")

    def test_restart_path_reports_only_the_update(self, tmp_path):
        assert _run(tmp_path, no_restart=False) == "Settings updated."

    def test_caller_restart_reports_only_the_update(self, tmp_path):
        msg = _run(tmp_path, no_restart=True, config_restart_by_caller=True)
        assert msg == "Settings updated."


class TestRunningCheck:
    def test_running_state_is_checked_before_the_report(self):
        tasks = _tasks()
        check = _index(
            tasks, "Check whether the skipped restart left a running instance")
        assert check < _index(tasks, "Report settings applied")
        task = tasks[check]
        assert task["ansible.builtin.include_role"] == {
            "name": "orchestrator", "tasks_from": "check_running.yml"}
        when = " ".join(task["when"])
        assert "no_restart" in when
        assert "config_restart_by_caller" in when
        assert "_config_set_stage_only" in when
