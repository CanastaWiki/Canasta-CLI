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

    def _fail_tasks(self):
        return [t for t in self.tasks
                if "ansible.builtin.fail" in t or "fail" in t]

    def test_missing_remote_is_reported_only_when_git_ran(self):
        """`git config --get` exits 1 for an unset key; other codes mean
        git declined to read the repository, and the remedies below name
        'git rm --cached' — destructive against a good clone."""
        no_remote = [t for t in self._fail_tasks()
                     if "git rm --cached" in _fail_msg(t)]
        assert len(no_remote) == 1, (
            "expected exactly one 'no origin remote' failure, got %d"
            % len(no_remote))
        when = no_remote[0].get("when")
        conditions = when if isinstance(when, list) else [when]
        assert any("rc in [0, 1]" in str(c) for c in conditions), (
            "the 'no origin remote' failure must be gated on git having "
            "actually read the repository (rc in [0, 1]), not on any "
            "non-zero rc")

    def test_unreadable_repository_gets_its_own_message(self):
        unreadable = [t for t in self._fail_tasks()
                      if "rc not in [0, 1]" in str(t.get("when", ""))]
        assert len(unreadable) == 1, (
            "recovery must report a repository git could not read "
            "separately from one with no origin remote")
        msg = _fail_msg(unreadable[0])
        assert "git rm --cached" not in msg, (
            "removing the gitlink drops a working extension when the "
            "repository was merely unreadable")
        assert "chown" in msg, (
            "the message must name the ownership remedy, since that is "
            "what git's safe.directory check is refusing")


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
