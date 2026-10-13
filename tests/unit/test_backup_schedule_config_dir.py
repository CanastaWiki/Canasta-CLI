"""The scheduled backup job finds the registry the schedule was set from.

cron runs the job with a minimal environment: no CANASTA_CONFIG_DIR and no
XDG_CONFIG_HOME. An instance registered in a non-default registry was then
"not found" on every run. A local job names the registry on its command
line; a remote job uses the registry on its own host.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APPLY = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                     "backup_schedule_apply.yml")
FIRST = "Classify the schedule target"
LAST = "Assemble cron command"


def _command_building_tasks():
    with open(APPLY) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    return tasks[names.index(FIRST):names.index(LAST) + 1]


def _cron_cmd(tmp_path, config_dir, extra_vars=None):
    tasks = [t for t in _command_building_tasks()
             if "ansible.builtin.shell" not in t
             and "ansible.builtin.fail" not in t]
    tasks.append({"ansible.builtin.debug": {"msg": "CRON={{ _cron_cmd }}"}})
    play_vars = {"instance_id": "site", "instance_path": "/srv/site",
                 "canasta_cli_bin": "/usr/local/bin/canasta",
                 "retention": {"keep-daily": "7"},
                 "ansible_python_interpreter": sys.executable}
    play_vars.update(extra_vars or {})
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": play_vars, "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"),
               CANASTA_HOST_CRONTAB="", CANASTA_CONFIG_DIR=config_dir)
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if "CRON=" in ln)
    return line.split("CRON=", 1)[1].rstrip('"')


def test_local_job_names_the_registry_for_create_and_purge(tmp_path):
    cmd = _cron_cmd(tmp_path, "/opt/alt registry")
    assert ("CANASTA_CONFIG_DIR='/opt/alt registry' /usr/local/bin/canasta "
            "backup create -i site ") in cmd
    assert ("&& CANASTA_CONFIG_DIR='/opt/alt registry' /usr/local/bin/canasta "
            "backup purge -i site ") in cmd


def test_remote_job_leaves_the_registry_to_its_host(tmp_path):
    cmd = _cron_cmd(tmp_path, "/opt/alt", {"target_host": "prod1"})
    assert "CANASTA_CONFIG_DIR" not in cmd
    assert " backup create -i site " in cmd
