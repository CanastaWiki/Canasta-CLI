"""Guard: a maintenance-script config key recognized by `config set` must also
be forwarded to the container on BOTH orchestrators, or the set silently does
nothing (the value lands in .env but never reaches the pods/containers).

Each key in the "Maintenance scripts" group of canasta_known_keys must appear
in the Compose web service `environment:` block AND in the K8s
_k8s_pod_env_allowlist (which curates which .env keys reach the web pods via
the env ConfigMap).
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DEFAULTS = os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml")
K8S_SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_sync_config.yml")


def _maintenance_keys():
    data = yaml.safe_load(open(DEFAULTS))
    return {e["name"] for e in data["canasta_known_keys"]
            if e.get("group") == "Maintenance scripts"}


def _maintenance_defaults():
    data = yaml.safe_load(open(DEFAULTS))
    return {e["name"]: e.get("default", "") for e in data["canasta_known_keys"]
            if e.get("group") == "Maintenance scripts"}


def _compose_web_env_fallbacks():
    doc = yaml.safe_load(open(COMPOSE))
    fallbacks = {}
    for item in doc["services"]["web"]["environment"]:
        key, value = item.split("=", 1)
        m = re.fullmatch(r"\$\{%s:-(.*)\}" % re.escape(key), value)
        fallbacks[key] = m.group(1) if m else None
    return fallbacks


def _compose_web_env_keys():
    doc = yaml.safe_load(open(COMPOSE))
    env = doc["services"]["web"]["environment"]
    # Entries are "KEY=${KEY:-default}" strings.
    return {item.split("=", 1)[0] for item in env}


def _k8s_pod_env_allowlist():
    tasks = yaml.safe_load(open(K8S_SYNC))
    for t in tasks:
        sf = t.get("ansible.builtin.set_fact") or t.get("set_fact") or {}
        if "_k8s_pod_env_allowlist" in sf:
            return set(sf["_k8s_pod_env_allowlist"])
    return set()


class TestMaintenanceEnvForwarding:
    def test_group_is_populated(self):
        keys = _maintenance_keys()
        assert "MW_ENABLE_JOB_RUNNER" in keys, (
            "config set must recognize the maintenance-script control vars")

    def test_forwarded_to_compose_web(self):
        missing = _maintenance_keys() - _compose_web_env_keys()
        assert not missing, (
            "maintenance keys recognized by config set but NOT forwarded in the "
            "Compose web `environment:` block (config set would silently do "
            "nothing): %s" % sorted(missing))

    def test_forwarded_to_k8s_pod_env(self):
        missing = _maintenance_keys() - _k8s_pod_env_allowlist()
        assert not missing, (
            "maintenance keys recognized by config set but NOT in the K8s "
            "_k8s_pod_env_allowlist (they'd never reach the pods): %s"
            % sorted(missing))

    def test_compose_fallback_matches_documented_default(self):
        fallbacks = _compose_web_env_fallbacks()
        mismatched = {
            key: (default, fallbacks.get(key))
            for key, default in _maintenance_defaults().items()
            if fallbacks.get(key) != default
        }
        assert not mismatched, (
            "maintenance keys whose Compose ${KEY:-default} fallback differs "
            "from the default documented in canasta_known_keys "
            "(documented, compose): %s" % mismatched)
