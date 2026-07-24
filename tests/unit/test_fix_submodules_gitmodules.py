"""Regression guard for #1156: `gitops fix-submodules` must write clean
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
        for t in _walk(_load(FIX_SUBMODULES)):
            bi = t.get("ansible.builtin.blockinfile") or t.get("blockinfile")
            if isinstance(bi, dict) and ".gitmodules" in bi.get("path", ""):
                raise AssertionError(
                    "fix-submodules must not write .gitmodules with blockinfile "
                    "— it injects '# ANSIBLE MANAGED BLOCK' comments (#1156): %r"
                    % t.get("name"))

    def test_writes_gitmodules_with_git_config(self):
        writers = [
            t for t in _walk(_load(FIX_SUBMODULES))
            if "config" in _cmd_text(t) and ".gitmodules" in _cmd_text(t)
        ]
        assert writers, (
            "fix-submodules must write .gitmodules entries with "
            "`git config -f .gitmodules submodule.<name>.path/.url` (#1156)")
