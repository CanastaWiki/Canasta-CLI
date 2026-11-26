"""The Kubernetes gitops pull runs inside the block that sets local changes
aside and puts them back, and the Kubernetes push puts back what an
interrupted pull set aside before it looks for staged changes."""

import os

import pytest

from test_gitops_pull_carries_local_changes import _block, _load

PULLS = ("pull_kubernetes.yml",)


@pytest.mark.parametrize("name", PULLS)
def test_the_pull_runs_inside_the_set_aside_block(name):
    block = _block(name)
    inner = [t.get("name") for t in block["block"]]
    assert inner[0] == "Set local changes aside for the rebase"
    assert block["block"][0]["ansible.builtin.include_tasks"].endswith(
        "/_set_aside_local_changes.yml")
    assert any("pull --rebase" in str(t.get("ansible.builtin.shell", {}))
               for t in block["block"])
    assert "when" not in block, "every path out of the pull must put the changes back"
    always = block["always"]
    assert [os.path.basename(t["ansible.builtin.include_tasks"]) for t in always] == [
        "_put_back_local_changes.yml"]


@pytest.mark.parametrize("name", PULLS)
def test_submodules_are_updated_before_the_changes_are_put_back(name):
    inner = [t.get("name") for t in _block(name)["block"]]
    assert "Update submodules" in inner


@pytest.mark.parametrize("name", PULLS)
def test_an_interrupted_pull_is_put_back_first(name):
    tasks = _load(name)
    names = [t.get("name") for t in tasks]
    heal = names.index("Put back local changes an interrupted pull set aside")
    assert heal < names.index("Pull with local changes carried through")
    assert tasks[heal]["ansible.builtin.include_tasks"].endswith(
        "/_put_back_local_changes.yml")


@pytest.mark.parametrize("name", ("push_kubernetes.yml",))
def test_push_never_pushes_set_aside_changes(name):
    tasks = _load(name)
    names = [t.get("name") for t in tasks]
    heal = names.index("Put back local changes an interrupted pull set aside")
    assert heal < names.index("Check for staged changes")
