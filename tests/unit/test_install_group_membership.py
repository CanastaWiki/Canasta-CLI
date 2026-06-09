"""Regression tests for the install canasta-group membership hardening.

An empty operator resolution must not silently skip the group-add — that
left a host's 'canasta' group with no members, so `canasta upgrade`'s
self-update was silently skipped. install must warn when the operator
can't be resolved, and must always report the membership outcome (not only
when it changed).

Pure YAML-structure parsing.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INSTALL_CANASTA = os.path.join(
    REPO_ROOT, "roles", "install", "tasks", "canasta.yml",
)


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _tasks():
    with open(INSTALL_CANASTA) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


def _whens(task):
    w = task.get("when")
    if w is None:
        return []
    return w if isinstance(w, list) else [w]


class TestInstallGroupHardening:
    def test_warns_when_operator_unresolved(self):
        # A task must fire specifically when the operator is empty — the
        # group-add must not be the only handling of that case.
        assert any(
            any("_operator_user | length == 0" in str(c) for c in _whens(t))
            for t in _tasks()
        ), "install must warn (not silently skip) when operator is empty"

    def test_reports_membership_even_when_unchanged(self):
        # The membership outcome is reported even on a no-op, not only
        # `when: _operator_group_add is changed`.
        assert any(
            any("is not changed" in str(c) for c in _whens(t))
            for t in _tasks()
        ), "install must report membership even when already a member"
