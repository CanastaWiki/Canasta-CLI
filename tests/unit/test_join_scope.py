"""Regression guard for #1162: `gitops join` (Compose) must not push the fresh
instance's create-time default settings back into the shared repo.

A fresh `canasta create` generates Canasta's default config/settings/global/*.php
files. When that instance joins an existing repo that deleted them, a blind
`git add -A` stages those untracked defaults and pushes them back. join.yml must
stage only its own artifacts (hosts/hosts.yaml + hosts/<name>/vars.yaml); every
other tracked file was already reconciled to HEAD by the checkout step, and the
rendered/host-local files are gitignored.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JOIN = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "join.yml")


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


class TestJoinStagingScope:
    def test_no_whole_tree_add(self):
        offenders = [
            _cmd_text(t) for t in _walk(_load(JOIN))
            if "git add -A" in _cmd_text(t) or "git add ." in _cmd_text(t)
        ]
        assert not offenders, (
            "gitops join must not `git add -A` — it sweeps the fresh instance's "
            "untracked create-time default settings into the shared repo "
            "(#1162): %r" % offenders)

    def test_stages_only_host_registration_and_vars(self):
        adds = [_cmd_text(t) for t in _walk(_load(JOIN))
                if "git add" in _cmd_text(t)]
        assert adds, "join.yml must stage its host-registration artifacts"
        joined = " ".join(adds)
        assert "hosts/hosts.yaml" in joined, (
            "join must stage the host registration (hosts/hosts.yaml): %r"
            % adds)
        assert "vars.yaml" in joined, (
            "join must stage this host's vars.yaml: %r" % adds)
