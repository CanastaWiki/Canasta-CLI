"""--git-user-name and --git-user-email reach git config verbatim.

init and join set the operator's git identity on the target host. A value
with an apostrophe (O'Brien) or a dollar sign must be stored as typed, not
break the command line or be expanded.
"""

import os
import subprocess
import sys

import pytest
import yaml

from _gitops_flow import flow_tasks

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
FILES = ["init_compose.yml", "init_kubernetes.yml", "join.yml"]
NAME = "Sean O'Brien $HOME"
EMAIL = "o'brien@example.com"


def _identity_tasks(name):
    found = [t for t in flow_tasks(name) if t.get("name") in (
        "Set git user.name on target host", "Set git user.email on target host")]
    assert len(found) == 2, name
    return found


@pytest.mark.parametrize("name", FILES)
def test_identity_with_quotes_is_stored_verbatim(tmp_path, name):
    home = tmp_path / "home"
    home.mkdir()
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"git_user_name": NAME, "git_user_email": EMAIL,
                 "ansible_python_interpreter": sys.executable},
        "tasks": _identity_tasks(name),
    }]))
    env = dict(os.environ, HOME=str(home), ANSIBLE_NOCOLOR="1",
               ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"))
    env.pop("GIT_CONFIG_GLOBAL", None)
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    def get(key):
        return subprocess.run(
            ["git", "config", "--file", str(home / ".gitconfig"), key],
            capture_output=True, text=True, check=True).stdout.rstrip("\n")

    assert get("user.name") == NAME
    assert get("user.email") == EMAIL
