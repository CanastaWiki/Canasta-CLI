"""Guard: canasta's internal gitops commits must not depend on the
user's commit-signing config (#668).

If the environment has `commit.gpgsign = true` but no usable gpg, a bare
`git commit` aborts with "gpg failed to sign the data", breaking
init/join/push. Every internal commit therefore runs with
`-c commit.gpgsign=false`. These tests pin that across the role so a new
commit call site can't silently reintroduce the dependency.
"""

import glob
import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")


def _cmd(task):
    """The command string of a command/shell task, else ''."""
    for key in ("ansible.builtin.command", "command",
                "ansible.builtin.shell", "shell"):
        v = task.get(key)
        if isinstance(v, dict):
            return v.get("cmd", "") or " ".join(v.get("argv", []) or [])
        if isinstance(v, str):
            return v
    return ""


def _walk(tasks):
    """Yield every task, descending into block/rescue/always."""
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _commit_cmds():
    """(file, cmd) for every task that runs `git ... commit`."""
    for path in sorted(glob.glob(os.path.join(TASKS, "*.yml"))):
        with open(path) as f:
            tasks = yaml.safe_load(f) or []
        for task in _walk(tasks):
            cmd = _cmd(task)
            if "git" in cmd and "commit -m" in cmd:
                yield os.path.basename(path), cmd


def test_internal_commits_disable_gpgsign():
    commits = list(_commit_cmds())
    assert commits, "expected to find internal git commit invocations"
    offenders = [(f, c) for f, c in commits
                 if "commit.gpgsign=false" not in c]
    assert not offenders, (
        "these gitops commits don't disable gpg signing (#668); a user's "
        "commit.gpgsign=true without gpg would break them:\n"
        + "\n".join("  %s: %s" % (f, c) for f, c in offenders)
    )
