"""What `canasta gitops add` says about a path it cannot stage.

Paths are relative to the instance root. A path that names no file, or one
outside the instance, is reported in plain words rather than as git's raw
`fatal:` line, and an operator in a subdirectory of the instance is shown
the root-relative spelling of what they typed.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ADD = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "add.yml")


def _git(inst, *args):
    subprocess.run(["git", *args], cwd=inst, check=True, capture_output=True)


def _add(tmp_path, files, cwd):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    _git(inst, "init", "-q")
    (inst / "wikis.yaml.template").write_text("wikis: []\n")
    with open(ADD) as f:
        tasks = [t for t in yaml.safe_load(f)
                 if t.get("name") != "Resolve instance"]
    tasks_file = tmp_path / "add_tasks.yml"
    tasks_file.write_text(yaml.safe_dump(tasks))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "files": files,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": str(tasks_file)}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"),
               CANASTA_HOST_PWD=str(inst / cwd) if cwd else str(tmp_path))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


def test_missing_file_is_named_without_git_fatal(tmp_path):
    rc, out = _add(tmp_path, "nope.txt", cwd=None)
    assert rc != 0
    assert "nope.txt: no such file under the instance root" in out
    assert "fatal: pathspec" not in out
    assert "typed from" not in out


def test_subdirectory_shows_the_root_relative_path(tmp_path):
    rc, out = _add(tmp_path, "../wikis.yaml.template", cwd="config")
    assert rc != 0
    assert "../wikis.yaml.template: outside the instance" in out
    assert "typed from config/, that is wikis.yaml.template" in out
    assert "outside repository" not in out


def test_root_relative_path_from_a_subdirectory_still_stages(tmp_path):
    rc, out = _add(tmp_path, "wikis.yaml.template", cwd="config")
    assert rc == 0, out
