"""The optional Redis service.

CANASTA_ENABLE_REDIS runs Redis as a main object cache the web
server and maintenance scripts share, so a purge from a maintenance
script reaches the web server. The flag is passed to the web container,
whose image chooses the cache; the CLI provides the service at
redis:6379 on both orchestrators.
"""

import os
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE = os.path.join(REPO_ROOT, "roles", "orchestrator", "files",
                       "compose", "docker-compose.yml")
CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")
PROFILES = os.path.join(REPO_ROOT, "vars", "compose_profiles.yml")
DEFAULTS = os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml")
SIDE_EFFECTS = os.path.join(REPO_ROOT, "roles", "config", "tasks",
                            "_side_effects.yml")
CLASSIFICATION = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def test_the_profile_is_off_by_default_and_owns_the_service():
    profiles = _load(PROFILES)
    assert {"profile": "redis", "flag": "CANASTA_ENABLE_REDIS",
            "default": "false"} in profiles["canasta_managed_profiles"]
    assert profiles["canasta_managed_profile_services"]["redis"] == [
        "redis"]


def test_the_compose_service_is_an_internal_bounded_cache():
    services = _load(COMPOSE)["services"]
    redis = services["redis"]
    assert redis["profiles"] == ["redis"]
    assert "ports" not in redis
    assert redis["command"] == [
        "redis-server", "--maxmemory", "${CANASTA_REDIS_MEMORY:-64}mb",
        "--maxmemory-policy", "allkeys-lru",
        "--save", "", "--appendonly", "no"]
    assert "CANASTA_ENABLE_REDIS=${CANASTA_ENABLE_REDIS:-false}" in (
        services["web"]["environment"])


def test_the_keys_are_known_and_gitops_keeps_them_per_host():
    keys = {k["name"]: k for k in _load(DEFAULTS)["canasta_known_keys"]}
    assert keys["CANASTA_ENABLE_REDIS"]["default"] == "false"
    assert keys["CANASTA_REDIS_MEMORY"]["default"] == "64"
    per_host = _load(CLASSIFICATION)["canasta_host_specific_nonsecret"]
    assert {"CANASTA_ENABLE_REDIS", "CANASTA_REDIS_MEMORY"} <= set(per_host)


def test_config_set_syncs_profiles_and_kubernetes_values():
    text = open(SIDE_EFFECTS).read()
    tasks = _load(SIDE_EFFECTS)
    sync = next(t for t in tasks
                if t.get("name") == "Sync COMPOSE_PROFILES / caddy image when feature flags change")
    assert "'CANASTA_ENABLE_REDIS'" in sync["when"][1]
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
