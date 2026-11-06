"""gitops init and join keep the git identity in the instance's repository.

--git-user-name and --git-user-email are stored in the gitops repository's
own config, verbatim, and never in the target host's global git config. The
identity check before any change accepts the flags or an identity the host
already has.
"""

import os
import subprocess
import sys

import pytest
import yaml

from _gitops_flow import flow_tasks

FILES = ["init_compose.yml", "init_kubernetes.yml", "join.yml"]
NAME = "Sean O'Brien $HOME"
EMAIL = "o'brien@example.com"
CHECK = ("Check git identity is configured", "Fail if git identity not configured")
STORE = ("Set git user.name in the gitops repository",
         "Set git user.email in the gitops repository")


def _tasks(flow, names):
    found = [t for t in flow_tasks(flow) if t.get("name") in names]
    assert len(found) == len(names), flow
    return found


def _play(tmp_path, tasks, play_vars):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(play_vars, ansible_python_interpreter=sys.executable),
        "tasks": tasks,
    }]))
    env = dict(os.environ, HOME=str(home), ANSIBLE_NOCOLOR="1",
               ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"),
               GIT_CONFIG_NOSYSTEM="1")
    for key in ("GIT_CONFIG_GLOBAL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        env.pop(key, None)
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    return proc, home


def _config(path, key):
    return subprocess.run(["git", "config", "--file", str(path), key],
                          capture_output=True, text=True).stdout.rstrip("\n")


@pytest.mark.parametrize("flow", FILES)
def test_identity_is_stored_verbatim_in_the_repository_only(tmp_path, flow):
    inst = tmp_path / "inst"
    inst.mkdir()
    subprocess.run(["git", "init", "-q", str(inst)], check=True)
    proc, home = _play(tmp_path, _tasks(flow, CHECK + STORE), {
        "instance_path": str(inst), "git_user_name": NAME,
        "git_user_email": EMAIL})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _config(inst / ".git" / "config", "user.name") == NAME
    assert _config(inst / ".git" / "config", "user.email") == EMAIL
    assert not (home / ".gitconfig").exists()


@pytest.mark.parametrize("flow", FILES)
def test_check_refuses_without_flags_or_an_identity(tmp_path, flow):
    proc, _ = _play(tmp_path, _tasks(flow, CHECK), {})
    assert proc.returncode != 0
    assert "Pass --git-user-name and --git-user-email" in proc.stdout + proc.stderr


@pytest.mark.parametrize("flow", FILES)
def test_check_accepts_an_identity_the_host_already_has(tmp_path, flow):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[user]\n\tname = Operator\n\temail = op@example.com\n")
    proc, _ = _play(tmp_path, _tasks(flow, CHECK), {})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _config(home / ".gitconfig", "user.name") == "Operator"
