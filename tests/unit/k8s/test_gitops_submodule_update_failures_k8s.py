"""`gitops pull` and `gitops join` on Kubernetes update submodules through
_update_submodules.yml, unconditionally and with no inline
`git submodule update` of their own, before pull renders the per-host
values and before join pushes."""

import os

import pytest

from _gitops_flow import flow_tasks
from test_gitops_submodule_update_failures import (
    GITOPS_TASKS,
    _includes_helper,
    _load,
    _names,
    _walk,
)

CALLERS = [
    "pull_kubernetes.yml",
    "join_kubernetes.yml",
]


class TestCallers:
    @pytest.mark.parametrize("filename", CALLERS)
    def test_caller_uses_the_helper(self, filename):
        tasks = _load(os.path.join(GITOPS_TASKS, filename))
        assert any(_includes_helper(t) for t in _walk(tasks))
        assert not any(
            "git submodule update" in str(t.get("ansible.builtin.command", ""))
            + str(t.get("ansible.builtin.shell", ""))
            for t in _walk(tasks))

    @pytest.mark.parametrize("filename", CALLERS)
    def test_helper_is_not_conditional(self, filename):
        tasks = _load(os.path.join(GITOPS_TASKS, filename))
        for t in tasks:
            if _includes_helper(t):
                assert "when" not in t

    def test_pull_kubernetes_updates_submodules_before_rendering(self):
        names = _names(
            _load(os.path.join(GITOPS_TASKS, "pull_kubernetes.yml")))
        assert names.index("Update submodules") < names.index(
            "Render per-host values")

    @pytest.mark.parametrize("filename", ["join_kubernetes.yml"])
    def test_join_updates_submodules_before_pushing(self, filename):
        tasks = flow_tasks(filename)
        names = _names(tasks)
        last_update = max(
            i for i, t in enumerate(_walk(tasks)) if _includes_helper(t))
        assert last_update < names.index("Push to remote")
