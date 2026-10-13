"""gitops init tells a refused deploy key apart from other ssh failures.

The Kubernetes init probes the deploy key with `git ls-remote`. Only a
refused key should lead to "register this public key"; a changed host key
or a connection error must fail with ssh's own error, since registering the
key cannot fix it. The Compose init shares the host key handling.
"""

import base64
import os
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "filter_plugins"))
from canasta_gitops import (  # noqa: E402
    canasta_git_remote_failure,
    canasta_git_remote_known_hosts_name,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
PROBE = os.path.join(TASKS, "_init_deploy_key_probe.yml")
ACCESS = os.path.join(TASKS, "_remote_access_error.yml")
PUBKEY = "ssh-ed25519 AAAAC3Nzatestkey canasta-test"

HOST_KEY_CHANGED = """\
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
Host key for [git.example.com]:2222 has changed and you have requested strict checking.
Host key verification failed.
fatal: Could not read from remote repository.
"""
HOST_KEY_FAILED = """\
Host key verification failed.
fatal: Could not read from remote repository.
"""
DENIED = """\
git@github.com: Permission denied (publickey).
fatal: Could not read from remote repository.
"""
NOT_FOUND = """\
ERROR: Repository not found.
fatal: Could not read from remote repository.
"""
DNS = """\
ssh: Could not resolve hostname git.example.com: Name or service not known
fatal: Could not read from remote repository.
"""


class TestClassify:
    @pytest.mark.parametrize("stderr,kind", [
        (HOST_KEY_CHANGED, "host_key"),
        (HOST_KEY_FAILED, "host_key"),
        (DENIED, "unauthorized"),
        (NOT_FOUND, "unauthorized"),
        (DNS, "other"),
        ("", "other"),
    ])
    def test_kinds(self, stderr, kind):
        assert canasta_git_remote_failure(stderr) == kind

    @pytest.mark.parametrize("repo,name", [
        ("git@github.com:org/repo.git", "github.com"),
        ("github.com:org/repo.git", "github.com"),
        ("ssh://git@git.example.com:2222/org/repo.git",
         "[git.example.com]:2222"),
        ("ssh://git@git.example.com:22/org/repo.git", "git.example.com"),
        ("ssh://git.example.com/org/repo.git", "git.example.com"),
        ("https://github.com/org/repo.git", ""),
    ])
    def test_known_hosts_name(self, repo, name):
        assert canasta_git_remote_known_hosts_name(repo) == name


def _stub_git(tmp_path, stderr, rc):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "stderr.txt").write_text(stderr)
    git = bindir / "git"
    git.write_text("#!/bin/sh\ncat '%s' >&2\nexit %d\n"
                   % (tmp_path / "stderr.txt", rc))
    git.chmod(0o755)
    return str(bindir)


def _run(tmp_path, tasks_file, stderr, rc, extra_vars):
    bindir = _stub_git(tmp_path, stderr, rc)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"ansible_python_interpreter": sys.executable},
                     **extra_vars),
        "tasks": [
            {"ansible.builtin.include_tasks": tasks_file},
            {"ansible.builtin.debug":
                {"msg": "RESULT={{ _remote_failure }}"}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               PATH=bindir + os.pathsep + os.environ.get("PATH", ""))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path, stdin=subprocess.DEVNULL)
    return proc, proc.stdout + proc.stderr


def _probe(tmp_path, stderr, rc, repo="git@github.com:org/repo.git"):
    return _run(tmp_path, PROBE, stderr, rc, {
        "repo": repo,
        "ssh_key": "",
        "_ssh_key_target_path": str(tmp_path / "key"),
        "_gitops_ssh_pubkey": {
            "content": base64.b64encode(PUBKEY.encode()).decode()},
        "_existing_is_ssh_key": True,
        "non_interactive": True,
    })


class TestK8sDeployKeyProbe:
    def test_authorized_key_skips_the_prompt(self, tmp_path):
        proc, out = _probe(tmp_path, "", 0)
        assert proc.returncode == 0, out
        assert "RESULT=ok" in out
        assert PUBKEY not in out

    @pytest.mark.parametrize("stderr", [DENIED, NOT_FOUND])
    def test_refused_key_asks_for_registration(self, tmp_path, stderr):
        proc, out = _probe(tmp_path, stderr, 128)
        assert proc.returncode != 0
        assert "not authorized" in out
        assert PUBKEY in out

    def test_changed_host_key_names_host_and_fix(self, tmp_path):
        proc, out = _probe(tmp_path, HOST_KEY_CHANGED, 128,
                           repo="ssh://git@git.example.com:2222/org/repo.git")
        assert proc.returncode != 0
        assert "ssh-keygen -R '[git.example.com]:2222'" in out
        assert "REMOTE HOST IDENTIFICATION HAS CHANGED" in out
        assert "not authorized" not in out
        assert PUBKEY not in out

    def test_host_key_verification_failure(self, tmp_path):
        proc, out = _probe(tmp_path, HOST_KEY_FAILED, 128)
        assert proc.returncode != 0
        assert "ssh-keygen -R 'github.com'" in out
        assert PUBKEY not in out

    def test_connection_error_shows_ssh_error(self, tmp_path):
        proc, out = _probe(tmp_path, DNS, 128)
        assert proc.returncode != 0
        assert "Could not resolve hostname git.example.com" in out
        assert "not authorized" not in out
        assert PUBKEY not in out


class TestComposeHostKeyCheck:
    """Compose init fails on a host key error before its generic remote
    check, which still covers refused keys and connection errors."""

    def _check(self, tmp_path, stderr):
        return _run(tmp_path, os.path.join(tmp_path, "compose.yml"), stderr,
                    128, {"repo": "git@github.com:org/repo.git"})

    def _write(self, tmp_path):
        (tmp_path / "compose.yml").write_text(yaml.safe_dump([
            {"name": "ls-remote", "ansible.builtin.command":
                {"cmd": "git ls-remote {{ repo }}"},
             "register": "_remote_check", "failed_when": False},
            {"ansible.builtin.include_tasks": ACCESS,
             "vars": {"_remote_result": "{{ _remote_check }}",
                      "_remote_fail_kinds": ["host_key"]}},
        ]))

    def test_host_key_failure_fails_with_fix(self, tmp_path):
        self._write(tmp_path)
        proc, out = self._check(tmp_path, HOST_KEY_CHANGED)
        assert proc.returncode != 0
        assert "ssh-keygen -R 'github.com'" in out

    @pytest.mark.parametrize("stderr,kind", [
        (DENIED, "unauthorized"), (DNS, "other")])
    def test_other_failures_are_left_to_the_caller(self, tmp_path, stderr,
                                                   kind):
        self._write(tmp_path)
        proc, out = self._check(tmp_path, stderr)
        assert proc.returncode == 0, out
        assert "RESULT=" + kind in out

    def test_compose_init_wires_the_host_key_check(self):
        with open(os.path.join(TASKS, "init_compose.yml")) as f:
            tasks = yaml.safe_load(f)
        names = [t.get("name", "") for t in tasks]
        inc = next(t for t in tasks
                   if t.get("ansible.builtin.include_tasks")
                   == "_remote_access_error.yml")
        assert inc["vars"]["_remote_fail_kinds"] == ["host_key"]
        assert inc["vars"]["_remote_result"] == "{{ _remote_check }}"
        assert names.index(inc["name"]) < names.index(
            "Fail early if the remote is unreachable or unauthorized")
