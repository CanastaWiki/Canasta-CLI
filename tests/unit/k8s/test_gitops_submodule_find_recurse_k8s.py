"""Kubernetes gitops init converts loose extension and skin checkouts to
submodules: its `.git` finds recurse one level, bounded by depth 2, and the
find and convert tasks are the same as Compose init's."""

import os

import pytest

from _gitops_flow import flow_tasks
from test_gitops_submodule_find_recurse import (
    INIT_COMPOSE,
    REPO_ROOT,
    _check_depth_bounds_the_recursion,
    _check_finds_exist,
    _check_recurse_true,
    _walk_tasks,
)


def _tasks_by_name(path):
    return {t.get("name"): t
            for t in _walk_tasks(flow_tasks(os.path.basename(path)))}


INIT_K8S = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "init_kubernetes.yml")
FILES = (INIT_K8S,)


class TestSubmoduleFindRecurses:
    @pytest.mark.parametrize("path", FILES, ids=os.path.basename)
    def test_finds_exist(self, path):
        _check_finds_exist(path)

    @pytest.mark.parametrize("path", FILES, ids=os.path.basename)
    def test_recurse_true(self, path):
        _check_recurse_true(path)

    @pytest.mark.parametrize("path", FILES, ids=os.path.basename)
    def test_depth_bounds_the_recursion(self, path):
        _check_depth_bounds_the_recursion(path)


class TestKubernetesInitMatchesCompose:
    NAMES = (
        "Find git repos in extensions",
        "Convert extension git repos to submodules",
        "Find git repos in skins",
        "Convert skin git repos to submodules",
    )

    def test_find_and_convert_tasks_are_identical(self):
        compose = _tasks_by_name(INIT_COMPOSE)
        k8s = _tasks_by_name(INIT_K8S)
        for name in self.NAMES:
            assert name in compose and name in k8s, name
            assert k8s[name] == compose[name], name
