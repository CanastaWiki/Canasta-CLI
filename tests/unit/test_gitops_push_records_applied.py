"""Compose gitops push records the commit it leaves the instance on.

The commit push makes is of files the instance already runs. Without
recording it in .gitops-applied, the next pull sees HEAD differ from the
applied commit and reports the previous pull as unfinished.
"""

import os
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PUSH_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml",
)


def _direct_block():
    with open(PUSH_COMPOSE) as f:
        tasks = yaml.safe_load(f)
    for t in tasks:
        if isinstance(t, dict) and t.get("name") == "Direct push to main":
            return t["block"]
    raise AssertionError("'Direct push to main' block missing/renamed")


def _index(tasks, name):
    names = [t.get("name") for t in tasks]
    assert name in names, "task %r missing/renamed" % name
    return names.index(name)


def test_applied_commit_is_recorded_between_commit_and_push():
    tasks = _direct_block()
    record = tasks[_index(tasks, "Record HEAD as the applied commit")]
    shell = record["ansible.builtin.shell"]
    assert shell["cmd"] == "git rev-parse HEAD > .gitops-applied"
    assert shell["chdir"] == "{{ instance_path }}"
    assert "_push_applied_is_local.rc == 0" in str(record.get("when"))
    assert _index(tasks, "Commit staged changes") < \
        _index(tasks, "Record HEAD as the applied commit") < \
        _index(tasks, "Push to main")


def test_locality_check_runs_before_the_commit():
    tasks = _direct_block()
    check = tasks[_index(tasks, "Check that every commit since the last apply is local")]
    assert check.get("register") == "_push_applied_is_local"
    assert check.get("failed_when") is False
    assert _index(tasks, "Check that every commit since the last apply is local") < \
        _index(tasks, "Commit staged changes")


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(repo, name):
    with open(os.path.join(repo, name), "w") as f:
        f.write(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _check_rc(repo):
    tasks = _direct_block()
    check = tasks[_index(tasks, "Check that every commit since the last apply is local")]
    cmd = check["ansible.builtin.shell"]["cmd"]
    # Folded scalar: newlines become spaces, as Ansible passes it to sh.
    return subprocess.run(
        ["sh", "-c", " ".join(cmd.split("\n"))], cwd=repo,
        capture_output=True,
    ).returncode


@pytest.fixture
def hosts(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    a = tmp_path / "a"
    b = tmp_path / "b"
    _git(tmp_path, "clone", "-q", str(origin), str(a))
    _git(a, "checkout", "-q", "-b", "main")
    base = _commit(a, "base")
    _git(a, "push", "-q", "-u", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(b))
    for repo in (a, b):
        (repo / ".gitops-applied").write_text(base)
    return str(a), str(b)


def test_local_commits_only_allow_recording(hosts):
    a, _ = hosts
    _commit(a, "local")
    assert _check_rc(a) == 0


def test_rejected_push_still_allows_recording(hosts):
    a, b = hosts
    _commit(b, "remote")
    _git(b, "push", "-q")
    _commit(a, "local")
    _git(a, "fetch", "-q")
    assert _check_rc(a) == 0


def test_unfinished_pull_blocks_recording(hosts):
    a, b = hosts
    _commit(b, "remote")
    _git(b, "push", "-q")
    _commit(a, "local")
    # A pull that rebased and then stopped before applying.
    _git(a, "pull", "-q", "--rebase")
    assert _check_rc(a) != 0


def test_missing_applied_file_blocks_recording(hosts):
    a, _ = hosts
    os.remove(os.path.join(a, ".gitops-applied"))
    _commit(a, "local")
    assert _check_rc(a) != 0
