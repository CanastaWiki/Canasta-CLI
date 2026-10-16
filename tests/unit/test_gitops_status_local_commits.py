"""gitops status tells local commits waiting to be pushed apart from a pull
that stopped before applying a commit from the remote."""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers, gitops  # noqa: E402

_STOPPED = "The last pull stopped before applying commit"


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


def _status(repo):
    script = gitops._gitops_status_script(str(repo))
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(repo.parent))
    out = subprocess.run(["sh", "-c", script], capture_output=True,
                         text=True, timeout=30, env=env).stdout
    return gitops._parse_gitops_status(out, "mysite")


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
        # Written without a trailing newline, as pull does.
        (repo / ".gitops-applied").write_text(base)
    return a, b


def test_local_commit_is_not_a_stopped_pull(hosts):
    a, _ = hosts
    _commit(a, "local")
    result = _status(a)
    assert _STOPPED not in result
    assert "Ahead of remote by 1 commit(s)." in result


def test_local_commit_with_remote_commits_waiting_is_not_a_stopped_pull(hosts):
    a, b = hosts
    _commit(b, "remote")
    _git(b, "push", "-q")
    _commit(a, "local")
    result = _status(a)
    assert _STOPPED not in result
    assert "Diverged from remote" in result


def test_pull_that_stopped_before_applying_is_reported(hosts):
    a, b = hosts
    _commit(b, "remote")
    _git(b, "push", "-q")
    _git(a, "pull", "-q")
    head = _git(a, "rev-parse", "--short", "HEAD")
    assert "%s %s." % (_STOPPED, head) in _status(a)


def test_rebased_pull_that_stopped_is_reported(hosts):
    a, b = hosts
    _commit(b, "remote")
    _git(b, "push", "-q")
    _commit(a, "local")
    _git(a, "pull", "-q", "--rebase")
    assert _STOPPED in _status(a)


def test_parser_reads_applied_commit_and_marker():
    d = _helpers._SENTINEL + "\n"
    out = d.join(["host", "MISSING", "def5678", "abc1234\nLOCAL_ONLY",
                  "", "", "FETCH:ok\n1\t0"]) + "\n"
    result = gitops._parse_gitops_status(out, "mysite")
    assert "Last applied:   abc1234" in result
    assert _STOPPED not in result
