"""Run the tail of the add or remove playbook (from the wikis.yaml or
per-wiki settings change on) as a localhost play against a stub
orchestrator: exec, restart and the gitops staging tasks only log what they
were asked to do."""

import os
import subprocess
import sys

import yaml


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
REMOVE = os.path.join(REPO_ROOT, "playbooks", "remove.yml")
ADD_FIRST = "Add wiki to wikis.yaml"
REMOVE_FIRST = "Remove per-wiki settings directory"


def _log(log, line):
    return {"ansible.builtin.shell": "printf '%s\\n' \"$L\" >> " + str(log),
            "environment": {"L": line}}


def _stub_root(tmp_path, running):
    log = tmp_path / "log"
    root = tmp_path / "root"
    stubs = {
        "orchestrator": {
            "exec.yml": [_log(log, "EXEC {{ exec_command }}")],
            "update_config.yml": [_log(log, "UPDATE_CONFIG")],
            "check_running.yml": [{"ansible.builtin.set_fact": {
                "_container_running": running}}],
        },
        "instance_lifecycle": {"restart.yml": [_log(log, "RESTART")]},
        "gitops": {
            "stage_new_wiki.yml": [_log(
                log, "STAGE")],
            "unstage_removed_wiki.yml": [_log(
                log, "UNSTAGE")],
        },
    }
    for role, files in stubs.items():
        tasks = root / "roles" / role / "tasks"
        tasks.mkdir(parents=True)
        for name, content in files.items():
            (tasks / name).write_text(yaml.safe_dump(content))
    return root, log


def _instance(tmp_path, gitops, wikis):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": [
        {"id": w, "url": "example.com/" + w if w != "main" else "example.com",
         "name": w} for w in wikis]}))
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
    return inst


def _tail(path, first):
    with open(path) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    return tasks[names.index(first):]


def _run(tmp_path, tasks, *, gitops=True, running=True, wikis,
         **play_vars):
    root, log = _stub_root(tmp_path, running)
    inst = _instance(tmp_path, gitops, wikis)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(canasta_root=str(root), instance_path=str(inst),
                     instance_id="inst", wiki="docs",
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH="%s:%s" % (root / "roles",
                                             os.path.join(REPO_ROOT, "roles")),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines() if log.exists() else []
    return proc.stdout, lines, inst


def _add(tmp_path, **kw):
    kw.setdefault("_add_domain", "example.com")
    kw.setdefault("_add_path", "docs")
    return _run(tmp_path, _tail(ADD, ADD_FIRST), wikis=["main"], **kw)


def _remove(tmp_path, **kw):
    kw.setdefault("_remove_dbs_left", [])
    return _run(tmp_path, _tail(REMOVE, REMOVE_FIRST), wikis=["main"], **kw)


def _updates(lines):
    return [ln for ln in lines if ln.startswith("EXEC php maintenance/update.php")]
