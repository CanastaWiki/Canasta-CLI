"""Run the tail of the upgrade role (from the restart decision on) as a
localhost play, with every included file stubbed out."""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "upgrade.yml")
FIRST = "Decide whether a restart is needed"


def _stub(task):
    """Replace an include or the boot unit with a fact recording that it was
    reached."""
    if "block" in task:
        task["block"] = [_stub(t) for t in task["block"]]
        return task
    for key in ("ansible.builtin.include_role", "ansible.builtin.include_tasks"):
        if key in task:
            target = task.pop(key)
            if isinstance(target, dict):
                target = target.get("tasks_from") or target.get("file")
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "_test_calls": "{{ _test_calls + [%r] }}" % os.path.basename(
                    str(target).strip())}
    if "canasta_boot_unit" in task:
        task.pop("canasta_boot_unit")
        task["ansible.builtin.set_fact"] = {
            "_test_calls": "{{ _test_calls + ['canasta_boot_unit'] }}"}
    return task


def _summary_task():
    with open(PLAYBOOK) as f:
        play = yaml.safe_load(f)
    block = next(t for t in play if t.get("name") == "Upgrade each instance")
    return next(t for t in block["block"] if t.get("name") == "Report upgrade complete")


def _tail():
    with open(MAIN) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    return [_stub(t) for t in tasks[names.index(FIRST):]]


def _instance(tmp_path, gitops):
    inst = tmp_path / "inst"
    inst.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
    return inst


def _run(tmp_path, *, gitops=True, running=True, images_updated=True,
         image_changed=False, **extra_vars):
    inst = _instance(tmp_path, gitops)
    result = tmp_path / "result.json"
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "canasta_root": REPO_ROOT,
            "instance_path": str(inst),
            "instance_id": "inst",
            "_test_calls": [],
            "_was_running": running,
            "_images_updated": images_updated,
            "_upgrade_rebuilt_image_changed": image_changed,
            "_upgrade_build_failed_services": [],
            "_upgrade_instances": {"instances": {"inst": {}}},
            **extra_vars,
        },
        "tasks": _tail() + [_summary_task(), {
            "ansible.builtin.copy": {
                "content": "{{ {'calls': _test_calls} | to_json }}",
                "dest": str(result),
                "mode": "0644",
            },
        }],
    }]
    playfile = tmp_path / "play.yml"
    playfile.write_text(yaml.safe_dump(play, sort_keys=False))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(playfile)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = json.loads(result.read_text())["calls"]
    return proc.stdout, calls
