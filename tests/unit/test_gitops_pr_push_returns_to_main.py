"""A failed PR-mode `gitops push` leaves the checkout on main.

The feature branch has no upstream, so a checkout left on it breaks the next
`gitops pull` and every @{upstream} check. Runs the pull-request block of
push_compose.yml against a temp repo whose remote rejects the push.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PUSH_COMPOSE = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml")


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _pr_block():
    with open(PUSH_COMPOSE) as f:
        return next(t for t in yaml.safe_load(f)
                    if t.get("name") == "Push via pull request")


def test_failed_feature_push_switches_back_to_main(tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    _git(inst, "init", "-q", "-b", "main")
    (inst / "README").write_text("one\n")
    _git(inst, "add", "README")
    _git(inst, "commit", "-qm", "one")
    _git(inst, "remote", "add", "origin", str(tmp_path / "missing.git"))
    (inst / "README").write_text("two\n")
    _git(inst, "add", "README")

    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable,
                 "_push_pr_mode": True, "_push_has_staged": True,
                 "_push_hostname": "h1", "gitops_git_env": {}},
        "tasks": [_pr_block()],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert _git(inst, "branch", "--list", "gitops-h1-*"), (
        "the feature branch should have been created before the push failed")
    assert _git(inst, "branch", "--show-current") == "main"
