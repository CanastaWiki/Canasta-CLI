"""The HAProxy edge leaves Kubernetes instances alone.

A Kubernetes instance has its own ingress, so the edge's conversion plan
skips it and the upgrade prunes earlier bouncers' volumes only on Compose.
"""

import os

import yaml

from test_canasta_edge import _entry, _plain, mod


def test_kubernetes_instances_are_left_out_of_the_plan(tmp_path):
    one = _plain(tmp_path, "one")
    entry = dict(_entry("one", one), orchestrator="kubernetes")
    assert mod.plan_conversion([entry]) == {"convert": [], "rootless": [], "rated": [],
                                            "refuse": None, "held": []}


def test_the_upgrade_prunes_on_compose_only():
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    with open(os.path.join(repo, "playbooks", "_upgrade_single.yml")) as f:
        tasks = {t.get("name"): t for t in yaml.safe_load(f)}
    prune = tasks["Remove volumes earlier CrowdSec bouncers left behind"]
    assert prune["canasta_edge"]["state"] == "prune"
    assert prune["when"] == "not (instance_is_k8s | bool)"
