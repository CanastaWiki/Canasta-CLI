"""Kubernetes gitops init and join refuse a repo URL Argo CD can't clone.

Argo CD clones the gitops repo over SSH with the deploy key, so a local path
or an https URL must be refused up front, before init creates the deploy key,
.git or anything else a corrected re-run would trip over.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLES = os.path.join(REPO_ROOT, "roles")
COMMON = os.path.join(ROLES, "common")

REFUSAL = "is not an SSH repository URL"


def _run(tmp_path, tasks_from, repo):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "values.yaml").write_text("domains:\n- localhost\n")
    keys = tmp_path / "keys"
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = t\n\temail = t@example.com\n")

    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "canasta_root": REPO_ROOT,
            "instance_path": str(inst),
            "instance_id": "t1",
            "host_name": "h1",
            "repo": repo,
            "key": str(keys / "deploy"),
            "non_interactive": True,
        },
        "tasks": [{"ansible.builtin.include_role": {
            "name": "gitops", "tasks_from": tasks_from}}],
    }]))
    env = dict(
        os.environ,
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_SSH_COMMAND="false",
        ANSIBLE_NOCOLOR="1",
        ANSIBLE_LOCALHOST_WARNING="0",
        ANSIBLE_ROLES_PATH=ROLES,
        ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
        ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
        ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
    )
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path, timeout=300)
    return inst, keys, proc.returncode, proc.stdout + proc.stderr


@pytest.mark.parametrize("repo", [
    "/home/user/gitops.git",
    "file:///home/user/gitops.git",
    "https://github.com/org/gitops.git",
])
def test_init_refuses_non_ssh_repo_before_creating_anything(tmp_path, repo):
    inst, keys, rc, out = _run(tmp_path, "init_kubernetes", repo)
    assert rc != 0, out
    assert REFUSAL in out, out
    assert "Argo CD clones" in out, out
    assert sorted(p.name for p in inst.iterdir()) == ["values.yaml"], out
    assert not keys.exists(), out


@pytest.mark.parametrize("repo", [
    "git@nonexistent.invalid:org/gitops.git",
    "ssh://git@nonexistent.invalid/org/gitops.git",
    "ssh://git@nonexistent.invalid:2222/org/gitops.git",
])
def test_init_accepts_ssh_repo(tmp_path, repo):
    # The remote is unreachable, so init still fails, but at the remote
    # access check further on rather than at the URL check.
    _, _, rc, out = _run(tmp_path, "init_kubernetes", repo)
    assert rc != 0, out
    assert REFUSAL not in out, out
    assert "Cannot access remote" in out, out


@pytest.mark.parametrize("repo", [
    "/home/user/gitops.git",
    "https://github.com/org/gitops.git",
])
def test_join_refuses_non_ssh_repo_before_creating_anything(tmp_path, repo):
    inst, _, rc, out = _run(tmp_path, "join_kubernetes", repo)
    assert rc != 0, out
    assert REFUSAL in out, out
    assert sorted(p.name for p in inst.iterdir()) == ["values.yaml"], out
