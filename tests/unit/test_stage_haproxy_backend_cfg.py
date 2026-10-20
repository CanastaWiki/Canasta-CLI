"""The upgrade stages config/haproxy-backend.cfg in a gitops repo that lacks it.

'gitops init' tracks the file with the rest of config/, but in a Compose
gitops repo created before the HAProxy edge the upgrade writes it afterwards,
and untracked it never reaches the repository.
"""
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "stage_haproxy_backend_cfg.yml")
UPGRADE = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
CFG = "config/haproxy-backend.cfg"


def _git(inst, *args):
    return subprocess.run(["git", *args], cwd=inst, capture_output=True,
                          text=True, check=True).stdout


def _repo(tmp_path, gitops=True, cfg=True, track=False, ignore=False):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    _git(inst, "init", "-q")
    (inst / ".gitignore").write_text(CFG + "\n" if ignore else "")
    if gitops:
        (inst / ".gitops-host").write_text("h1\n")
    if cfg:
        (inst / CFG).write_text("# backend directives\n")
    _git(inst, "add", ".gitignore")
    if track:
        _git(inst, "add", CFG)
    _git(inst, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    return inst


def _run(tmp_path, inst):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(inst),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": TASKS}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def _staged(inst):
    return _git(inst, "diff", "--cached", "--name-status")


def test_an_untracked_file_is_staged_and_reported(tmp_path):
    inst = _repo(tmp_path)
    out = _run(tmp_path, inst)
    assert _staged(inst) == "A\t" + CFG + "\n"
    assert "canasta gitops push" in out
    # Running again changes nothing.
    _run(tmp_path, inst)
    assert _staged(inst) == "A\t" + CFG + "\n"


def test_a_tracked_file_is_left_alone(tmp_path):
    inst = _repo(tmp_path, track=True)
    out = _run(tmp_path, inst)
    assert _staged(inst) == ""
    assert "canasta gitops push" not in out


def test_an_ignored_file_is_not_staged(tmp_path):
    inst = _repo(tmp_path, ignore=True)
    _run(tmp_path, inst)
    assert _staged(inst) == ""


def test_an_instance_without_gitops_is_left_alone(tmp_path):
    inst = _repo(tmp_path, gitops=False)
    _run(tmp_path, inst)
    assert _staged(inst) == ""


def test_a_missing_file_is_not_an_error(tmp_path):
    inst = _repo(tmp_path, cfg=False)
    _run(tmp_path, inst)
    assert _staged(inst) == ""


def test_the_upgrade_stages_it_after_writing_it_on_compose_only():
    with open(UPGRADE) as fh:
        tasks = yaml.safe_load(fh)
    names = [t.get("name") for t in tasks]
    stage = "Stage haproxy-backend.cfg in a gitops repo that lacks it"
    assert names.index("Initialize orchestrator config") < names.index(stage)
    task = tasks[names.index(stage)]
    assert task["ansible.builtin.include_tasks"].strip().endswith(
        "/roles/gitops/tasks/stage_haproxy_backend_cfg.yml")
    assert task["when"] == "not (instance_is_k8s | bool)"
