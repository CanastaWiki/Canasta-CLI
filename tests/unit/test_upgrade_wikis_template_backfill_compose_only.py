"""`canasta upgrade` backfills wikis.yaml.template on Compose only.

Only Compose gitops tracks wikis.yaml.template. Creating it on a Kubernetes
gitops instance leaves an untracked file that no push ever commits.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
GITOPS_VARS = os.path.join(REPO_ROOT, "roles", "gitops", "vars", "main.yml")


def _backfill_task():
    with open(MAIN) as f:
        tasks = yaml.safe_load(f)
    for t in tasks:
        if str(t.get("ansible.builtin.include_tasks", "")).strip().endswith(
                "/backfill_wikis_template.yml"):
            return t
    raise AssertionError("upgrade no longer includes backfill_wikis_template.yml")


def test_the_include_is_not_wrapped_in_a_block():
    assert "block" not in _backfill_task()


@pytest.mark.parametrize("variables, runs", [
    ({"instance_orchestrator": "compose"}, True),
    ({}, True),
    ({"instance_orchestrator": "kubernetes"}, False),
    ({"instance_orchestrator": "k8s"}, False),
])
def test_runs_only_on_compose(variables, runs):
    when = _backfill_task()["when"]
    templar = Templar(loader=DataLoader(), variables=variables)
    result = templar.template(trust_as_template("{{ " + when + " }}"))
    assert str(result).strip() == str(runs)


def test_only_compose_tracks_the_template():
    with open(GITOPS_VARS) as f:
        gitops_vars = yaml.safe_load(f)
    assert "wikis.yaml.template" in gitops_vars["gitops_compose_tracked_paths"]
    assert "wikis.yaml.template" not in gitops_vars["gitops_k8s_tracked_paths"]
