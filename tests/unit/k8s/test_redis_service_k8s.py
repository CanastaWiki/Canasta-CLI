"""The optional Redis service on Kubernetes.

config set writes the Redis flag and memory into the Helm values, the chart
runs a Redis Deployment and Service only when enabled, and the upgrade's
adoption of an instance's own Redis service is gated off Kubernetes.
"""

import os
import shutil
import subprocess

import pytest
import yaml

from test_redis_service import ADOPT, REPO_ROOT, SIDE_EFFECTS, _load

CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")


def test_config_set_syncs_kubernetes_values():
    text = open(SIDE_EFFECTS).read()
    assert "{'redis': {'enabled': _config_value | lower == 'true'}}" in text
    assert "{'redis': {'memory': _config_value | int}}" in text


def _render(*sets):
    args = ["helm", "template", "t", CHART]
    for s in sets:
        args += ["--set", s]
    return subprocess.run(args, capture_output=True, text=True,
                          check=True).stdout


@pytest.mark.skipif(not shutil.which("helm"), reason="helm not installed")
def test_the_chart_runs_redis_only_when_enabled():
    off = _render()
    assert "component: redis" not in off
    assert '- name: CANASTA_ENABLE_REDIS\n              value: "false"' in off
    on = _render("redis.enabled=true", "redis.memory=128")
    docs = [d for d in yaml.safe_load_all(on) if d]
    svc = next(d for d in docs if d["kind"] == "Service"
               and d["metadata"]["name"] == "redis")
    assert svc["spec"]["ports"][0]["port"] == 6379
    dep = next(d for d in docs if d["kind"] == "Deployment"
               and d["metadata"]["name"] == "t-redis")
    assert dep["spec"]["template"]["spec"]["containers"][0]["args"] == [
        "redis-server", "--maxmemory", "128mb",
        "--maxmemory-policy", "allkeys-lru",
        "--save", "", "--appendonly", "no"]
    assert '- name: CANASTA_ENABLE_REDIS\n              value: "true"' in on


def test_upgrade_redis_adoption_is_gated_off_kubernetes():
    note = _load(ADOPT)[0]
    decision = note["ansible.builtin.set_fact"]["_mig_own_redis"]
    assert "not (instance_is_k8s | bool)" in decision
