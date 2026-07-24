"""Regression guard: orphan-gitlink recovery, and its use by `gitops init`.

An orphan gitlink is a path staged/committed as a submodule (mode 160000) with
no .gitmodules entry; a later clone/pull leaves it empty, losing the
extension/skin. The shared task recovers a .gitmodules entry (or fails loudly),
and `gitops init` must run it before its initial commit so it never commits a
broken submodule.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RECOVER = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "_recover_orphan_gitlinks.yml")
INIT = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "init_compose.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _text(t):
    for key in ("ansible.builtin.command", "command",
                "ansible.builtin.shell", "shell"):
        c = t.get(key)
        if isinstance(c, dict):
            if c.get("argv"):
                return " ".join(str(a) for a in c["argv"])
            return c.get("cmd", "")
        if isinstance(c, str):
            return c
    return ""


def _fail_msg(t):
    f = t.get("ansible.builtin.fail") or t.get("fail") or {}
    return f.get("msg", "") if isinstance(f, dict) else ""


class TestOrphanRecoveryTask:
    def setup_method(self):
        self.tasks = list(_walk(_load(RECOVER)))

    def test_lists_gitlinks(self):
        assert any("160000" in _text(t) for t in self.tasks), (
            "recovery must inspect gitlinks (mode 160000) to catch orphans")

    def test_writes_gitmodules_via_git_config(self):
        assert any("config" in _text(t) and ".gitmodules" in _text(t)
                   for t in self.tasks), (
            "recovery must register orphans via `git config -f .gitmodules`")

    def test_fails_loudly_when_url_unrecoverable(self):
        assert any(".gitmodules" in _fail_msg(t) for t in self.tasks
                   if "ansible.builtin.fail" in t or "fail" in t), (
            "recovery must fail loudly when an orphan has no recoverable URL")


class TestInitUsesRecovery:
    def _recovery_and_commit_indices(self):
        tasks = list(_walk(_load(INIT)))
        recover_i = commit_i = -1
        for i, t in enumerate(tasks):
            inc = (t.get("ansible.builtin.include_tasks")
                   or t.get("include_tasks") or "")
            if "_recover_orphan_gitlinks.yml" in inc:
                recover_i = i
            if "commit" in _text(t) and "Initial gitops" in _text(t):
                commit_i = i
        return recover_i, commit_i

    def test_recovery_runs_before_initial_commit(self):
        recover_i, commit_i = self._recovery_and_commit_indices()
        assert recover_i >= 0, "init must include _recover_orphan_gitlinks.yml"
        assert commit_i >= 0
        assert recover_i < commit_i, (
            "orphan-gitlink recovery must run before the initial commit")
