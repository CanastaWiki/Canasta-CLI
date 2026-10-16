"""extension/skin add and remove ensure the instance is running without
announcing a start that never happens.

They include orchestrator start.yml with start_quiet_when_running, which
suppresses "Starting containers..." and "Instance is already running." on
an instance that is already up. `canasta start` keeps both messages.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
START = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")
EXT_TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")

MESSAGE_TASKS = (
    "Report starting containers",
    "Skip start when every expected service is running",
    "Report the services being started",
    "Decide whether containers need starting",
    "Report starting containers (quiet mode)",
)


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _message_tasks():
    with open(START) as f:
        tasks = list(_walk(yaml.safe_load(f)))
    by_name = {t.get("name"): t for t in tasks}
    return [by_name[n] for n in MESSAGE_TASKS if n in by_name]


def _run(tmp_path, running, quiet):
    play = tmp_path / "play.yml"
    play_vars = {"_container_running": running, "_missing_services": []}
    if quiet is not None:
        play_vars["start_quiet_when_running"] = quiet
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": play_vars,
        "tasks": _message_tasks(),
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
    return proc.stdout


class TestQuietWhenRunning:
    def test_running_instance_prints_no_start_messages(self, tmp_path):
        out = _run(tmp_path, running=True, quiet=True)
        assert "Starting containers..." not in out
        assert "Instance is already running." not in out

    def test_stopped_instance_still_reports_the_start(self, tmp_path):
        out = _run(tmp_path, running=False, quiet=True)
        assert out.count("Starting containers...") == 1


class TestCanastaStartUnchanged:
    def test_running_instance_reports_already_running(self, tmp_path):
        out = _run(tmp_path, running=True, quiet=None)
        assert "Starting containers..." in out
        assert "Instance is already running." in out

    def test_stopped_instance_reports_the_start_once(self, tmp_path):
        out = _run(tmp_path, running=False, quiet=None)
        assert out.count("Starting containers...") == 1
        assert "Instance is already running." not in out


class TestExtensionCallersAreQuiet:
    def _start_includes(self, path):
        with open(path) as f:
            tasks = list(_walk(yaml.safe_load(f)))
        return [t for t in tasks
                if (t.get("ansible.builtin.include_role") or {}).get(
                    "tasks_from") == "start.yml"]

    def test_add_and_remove_pass_the_quiet_flag(self):
        for name in ("add.yml", "remove.yml"):
            includes = self._start_includes(os.path.join(EXT_TASKS, name))
            assert includes, name
            for task in includes:
                assert (task.get("vars") or {}).get(
                    "start_quiet_when_running") is True, name
