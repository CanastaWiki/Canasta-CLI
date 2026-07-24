"""Regression guard: `gitops init` must never commit an orphan
gitlink (a staged mode-160000 submodule with no .gitmodules entry), which
silently loses the extension/skin on the next clone.

After `git add -A`, init must recover a .gitmodules entry for each orphan
gitlink from its clone's origin URL — or fail loudly when the URL is
unavailable — before the initial commit.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INIT = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "init_compose.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    """Flatten tasks, preserving order (a block's children follow the block)."""
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


class TestInitRecoversOrphanGitlinks:
    def setup_method(self):
        self.tasks = list(_walk(_load(INIT)))

    def _index(self, predicate):
        for i, t in enumerate(self.tasks):
            if predicate(t):
                return i
        return -1

    def test_lists_staged_gitlinks(self):
        assert any("160000" in _text(t) for t in self.tasks), (
            "init_compose.yml must inspect staged gitlinks (mode 160000) so it "
            "can catch unconverted extensions/skins")

    def test_writes_gitmodules_for_orphans(self):
        assert any("config" in _text(t) and ".gitmodules" in _text(t)
                   for t in self.tasks), (
            "init must register recovered orphans in .gitmodules via "
            "`git config -f .gitmodules`")

    def test_fails_loudly_when_url_unrecoverable(self):
        guards = [t for t in self.tasks
                  if ("ansible.builtin.fail" in t or "fail" in t)
                  and ".gitmodules" in _fail_msg(t)]
        assert guards, (
            "init must fail loudly when an orphan gitlink has no recoverable "
            "URL, rather than commit a broken submodule")

    def test_recovery_runs_before_initial_commit(self):
        gitmodules_i = self._index(
            lambda t: "config" in _text(t) and ".gitmodules" in _text(t))
        commit_i = self._index(
            lambda t: "commit" in _text(t) and "Initial gitops" in _text(t))
        assert gitmodules_i >= 0 and commit_i >= 0
        assert gitmodules_i < commit_i, (
            "the orphan-gitlink recovery must run before the initial commit, "
            "so the commit records proper submodules")
