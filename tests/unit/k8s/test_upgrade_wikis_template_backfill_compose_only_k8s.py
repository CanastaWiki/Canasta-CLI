"""`canasta upgrade` does not backfill wikis.yaml.template on Kubernetes.

Kubernetes gitops does not track wikis.yaml.template, so creating it there
leaves an untracked file that no push ever commits.
"""

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _orchestrator import TEMPLAR_VARS
from test_upgrade_wikis_template_backfill_compose_only import GITOPS_VARS, _backfill_task


@pytest.mark.parametrize("variables, runs", [
    ({"instance_orchestrator": "kubernetes"}, False),
    ({"instance_orchestrator": "k8s"}, False),
])
def test_runs_only_on_compose(variables, runs):
    when = _backfill_task()["when"]
    templar = Templar(loader=DataLoader(), variables=dict(TEMPLAR_VARS, **variables))
    result = templar.template(trust_as_template("{{ " + when + " }}"))
    assert str(result).strip() == str(runs)


def test_kubernetes_does_not_track_the_template():
    with open(GITOPS_VARS) as f:
        gitops_vars = yaml.safe_load(f)
    assert "wikis.yaml.template" not in gitops_vars["gitops_k8s_tracked_paths"]
