"""The Argo CD host-key scan gets a bare host and port from the repo URL."""

import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                     "_apply_argo_repo_secret.yml")


def _host_port_task():
    with open(TASKS) as f:
        for task in yaml.safe_load(f):
            if task.get("name") == "Determine the gitops repo SSH host and port":
                return task
    raise AssertionError("host/port task not found")


@pytest.mark.parametrize("repo, host, port", [
    ("ssh://git@git.example.com:2222/srv/git/config.git", "git.example.com", "2222"),
    ("ssh://git@git.example.com/srv/git/config.git", "git.example.com", "22"),
    ("git@git.example.com:org/config.git", "git.example.com", "22"),
])
def test_host_and_port(tmp_path, repo, host, port):
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"repo": repo},
        "tasks": [
            _host_port_task(),
            {"ansible.builtin.copy": {
                "content": "{{ {'host': _repo_host, 'port': _repo_port} | to_json }}",
                "dest": str(out)}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(out.read_text()) == {"host": host, "port": port}


def _merge_task():
    with open(TASKS) as f:
        for task in yaml.safe_load(f):
            for sub in task.get("block", []):
                if sub.get("name") == "Merge the host key into Argo CD known-hosts":
                    return sub
    raise AssertionError("known-hosts merge task not found")


def test_keyscan_comment_lines_are_not_merged(tmp_path):
    merge = _merge_task()
    out = tmp_path / "out.txt"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "_argo_known_hosts": {"resources": [
                {"data": {"ssh_known_hosts": "h1 k1\n"}}]},
            "_repo_keyscan": {"stdout_lines": [
                "# git.example.com:2222 SSH-2.0-OpenSSH_9.7",
                "[git.example.com]:2222 ssh-ed25519 AAAAone",
                "# git.example.com:2222 SSH-2.0-OpenSSH_9.7",
                "[git.example.com]:2222 ssh-rsa AAAAtwo",
                "",
            ]},
        },
        "tasks": [{
            "vars": merge["vars"],
            "ansible.builtin.copy": {
                "content": merge["kubernetes.core.k8s"]["definition"]["data"]
                ["ssh_known_hosts"],
                "dest": str(out)},
        }],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = out.read_text().splitlines()
    assert lines[0] == "h1 k1"
    assert sorted(lines[1:]) == [
        "[git.example.com]:2222 ssh-ed25519 AAAAone",
        "[git.example.com]:2222 ssh-rsa AAAAtwo",
    ]
