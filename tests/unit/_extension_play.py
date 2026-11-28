"""Run the tail of the extensions_skins role's enable.yml or disable.yml (from
the gitops settings commit on) as a localhost play against stubs: the settings
commit and update.php only log what they were asked to do."""

import os
import subprocess
import sys

import yaml


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")
ENABLE = (os.path.join(TASKS, "enable.yml"),
          "Commit {{ _item_type }} enable (gitops)")
DISABLE = (os.path.join(TASKS, "disable.yml"),
           "Commit {{ _item_type }} disable (gitops)")


def _log(log, line):
    return {"ansible.builtin.shell": "printf '%s\\n' \"$L\" >> " + str(log),
            "environment": {"L": line}}


def _stub_root(tmp_path):
    log = tmp_path / "log"
    root = tmp_path / "root"
    stubs = {
        "extensions_skins": {
            "_commit_settings.yml": [_log(log, "COMMIT {{ _settings_action }}")],
        },
        "mediawiki": {
            "run_update_php.yml": [_log(
                log, "UPDATE {{ update_php_wikis | join(',') }}")],
        },
    }
    for role, files in stubs.items():
        tasks = root / "roles" / role / "tasks"
        tasks.mkdir(parents=True)
        for name, content in files.items():
            (tasks / name).write_text(yaml.safe_dump(content))
    return root, log


def _tail(which):
    path, first = which
    with open(path) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    return tasks[names.index(first):]


def _run(tmp_path, which, *, gitops=True, changed=True, **play_vars):
    root, log = _stub_root(tmp_path)
    inst = tmp_path / "inst"
    inst.mkdir()
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(canasta_root=str(root), instance_path=str(inst),
                     instance_id="inst", _item_type="extensions",
                     _names="Cite,Foo", _settings_change={"changed": changed},
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": _tail(which),
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH="%s:%s" % (root / "roles",
                                             os.path.join(REPO_ROOT, "roles")),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY="%s:%s" % (
                   os.path.join(COMMON, "library"),
                   os.path.join(REPO_ROOT, "roles", "extensions_skins", "library")),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines() if log.exists() else []
    return proc.stdout, lines
