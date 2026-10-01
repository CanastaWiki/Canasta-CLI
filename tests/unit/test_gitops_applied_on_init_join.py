"""Compose gitops init and join record the commit they leave rendered.

`gitops pull` diffs against .gitops-applied to decide what a pull changed.
Without it, a first pull that fetched a commit and then failed would leave
its retry diffing against that fetched commit, so the restart, maintenance
and backup-schedule follow-ups for the commit's changes were never reported.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")


def _tasks(name):
    with open(os.path.join(TASKS, name)) as f:
        return [t for t in yaml.safe_load(f) if isinstance(t, dict)]


def _index(tasks, name):
    names = [t.get("name") for t in tasks]
    assert name in names, "task %r missing/renamed" % name
    return names.index(name)


@pytest.mark.parametrize("flow", ["init_compose.yml", "join.yml"])
def test_applied_commit_is_recorded_after_the_push(flow):
    tasks = _tasks(flow)
    record = tasks[_index(tasks, "Record HEAD as the applied commit")]
    shell = record["ansible.builtin.shell"]
    assert shell["cmd"] == "git rev-parse HEAD > .gitops-applied"
    assert shell["chdir"] == "{{ instance_path }}"
    assert "when" not in record
    assert _index(tasks, "Push to remote") < \
        _index(tasks, "Record HEAD as the applied commit")
