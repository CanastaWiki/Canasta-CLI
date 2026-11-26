"""Regression guard: the extension/skin submodule-conversion `find` must recurse.

Loose extension/skin checkouts live at ``<dir>/<Name>/.git`` — one level below
the search root ``<instance>/extensions`` (or ``skins``). With ``recurse: false``
the find only examines the immediate directory and matches nothing, so the
`_convert_submodule.yml` loop is empty: loosely-cloned extensions are committed
as orphan gitlinks (no ``.gitmodules``) and `git submodule status` fails on the
source. These finds must use ``recurse: true`` (bounded by ``depth: 2`` to that
one level and to skip a nested submodule's own ``.git``).
"""

import os

import pytest

from _gitops_flow import flow_tasks

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INIT_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "init_compose.yml")
FILES = (
    INIT_COMPOSE,
    os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "fix_submodules.yml"),
)


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _submodule_find_tasks(path):
    """Yield (task_name, find_args) for every find that looks for a `.git`
    directory under extensions/ or skins/."""
    for task in _walk_tasks(flow_tasks(os.path.basename(path))):
        find = task.get("ansible.builtin.find") or task.get("find")
        if not isinstance(find, dict):
            continue
        paths = str(find.get("paths", ""))
        if find.get("patterns") == ".git" and (
            "/extensions" in paths or "/skins" in paths
        ):
            yield task.get("name", "<unnamed>"), find


def _check_finds_exist(path):
    names = [n for n, _ in _submodule_find_tasks(path)]
    assert any("extension" in n.lower() for n in names), (
        f"{os.path.basename(path)} has no extensions .git find"
    )
    assert any("skin" in n.lower() for n in names), (
        f"{os.path.basename(path)} has no skins .git find"
    )


def _check_recurse_true(path):
    for name, find in _submodule_find_tasks(path):
        assert find.get("recurse") is True, (
            f"{os.path.basename(path)} task '{name}': "
            f"recurse must be true, got {find.get('recurse')!r}"
        )


def _check_depth_bounds_the_recursion(path):
    for name, find in _submodule_find_tasks(path):
        assert find.get("depth") == 2, (
            f"{os.path.basename(path)} task '{name}': "
            f"expected depth 2, got {find.get('depth')!r}"
        )


class TestSubmoduleFindRecurses:
    @pytest.mark.parametrize("path", FILES, ids=os.path.basename)
    def test_finds_exist(self, path):
        """Each file must still have extension and skin `.git` finds (guards
        against the tasks being renamed/removed without updating this guard)."""
        _check_finds_exist(path)

    @pytest.mark.parametrize("path", FILES, ids=os.path.basename)
    def test_recurse_true(self, path):
        """recurse must be true or the converter never matches
        <dir>/<Name>/.git and is dead code."""
        _check_recurse_true(path)

    @pytest.mark.parametrize("path", FILES, ids=os.path.basename)
    def test_depth_bounds_the_recursion(self, path):
        """depth: 2 keeps the recursion to <dir>/<Name>/.git and excludes a
        nested submodule's own .git."""
        _check_depth_bounds_the_recursion(path)
