"""Regression guard: `gitops fix-submodules` must write clean
.gitmodules stanzas, not blockinfile with "ANSIBLE MANAGED BLOCK" comments.

.gitmodules is a machine-read, git-managed file; the wrapper comments are noise
that obscures any legitimate annotation. Recovered entries must be written with
git's own config writer (git config -f .gitmodules).
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
FIX_SUBMODULES = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "fix_submodules.yml")
# The .gitmodules writes live in the shared orphan-recovery task that
# fix-submodules includes.
RECOVER = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "_recover_orphan_gitlinks.yml")


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


def _cmd_text(t):
    c = t.get("ansible.builtin.command") or t.get("command") or {}
    if not isinstance(c, dict):
        return str(c)
    if c.get("argv"):
        return " ".join(str(a) for a in c["argv"])
    return c.get("cmd", "")


class TestFixSubmodulesGitmodules:
    def test_no_blockinfile_on_gitmodules(self):
        for path in (FIX_SUBMODULES, RECOVER):
            for t in _walk(_load(path)):
                bi = t.get("ansible.builtin.blockinfile") or t.get("blockinfile")
                if isinstance(bi, dict) and ".gitmodules" in bi.get("path", ""):
                    raise AssertionError(
                        "must not write .gitmodules with blockinfile — it "
                        "injects '# ANSIBLE MANAGED BLOCK' comments: %r (%s)"
                        % (t.get("name"), path))

    def test_writes_gitmodules_with_git_config(self):
        writers = [
            t for t in _walk(_load(RECOVER))
            if "config" in _cmd_text(t) and ".gitmodules" in _cmd_text(t)
        ]
        assert writers, (
            "orphan recovery must write .gitmodules entries with "
            "`git config -f .gitmodules submodule.<name>.path/.url`")

    def test_fix_submodules_includes_shared_recovery(self):
        includes = [
            (t.get("ansible.builtin.include_tasks") or t.get("include_tasks") or "")
            for t in _walk(_load(FIX_SUBMODULES))
        ]
        assert any("_recover_orphan_gitlinks.yml" in inc for inc in includes), (
            "fix-submodules must include the shared _recover_orphan_gitlinks.yml")
