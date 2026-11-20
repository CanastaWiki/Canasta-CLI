"""PHP-FPM pool settings in .env reach the web container on both orchestrators.

The edge sizes its backend limits from PM_MAX_CHILDREN in .env, so PHP-FPM
has to read the same value, or the edge sends more requests than the pool
can take.
"""

import os
import re

import yaml

from canasta_edge_render import DEFAULT_PM_MAX_CHILDREN

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

# Image defaults from the CanastaBase Dockerfile.
POOL_KEYS = {
    "PHP_MEMORY_LIMIT": "256M",
    "PM_MAX_CHILDREN": "25",
    "PM_START_SERVERS": "10",
    "PM_MIN_SPARE_SERVERS": "5",
    "PM_MAX_SPARE_SERVERS": "15",
}


def _load(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return yaml.safe_load(f)


def _compose_web_env():
    compose = _load("roles", "orchestrator", "files", "compose", "docker-compose.yml")
    return dict(e.split("=", 1) for e in compose["services"]["web"]["environment"])


def _k8s_allowlist():
    tasks = _load("roles", "orchestrator", "tasks", "k8s_sync_config.yml")
    for task in tasks:
        facts = task.get("ansible.builtin.set_fact", {})
        if "_k8s_pod_env_allowlist" in facts:
            return facts["_k8s_pod_env_allowlist"]
    raise AssertionError("no _k8s_pod_env_allowlist in k8s_sync_config.yml")


def _known_keys():
    data = _load("roles", "config", "defaults", "main.yml")
    return {k["name"]: k for k in data["canasta_known_keys"]}


def test_compose_passes_pool_keys_with_image_defaults():
    env = _compose_web_env()
    for key, default in POOL_KEYS.items():
        assert env.get(key) == "${%s:-%s}" % (key, default), key


def test_k8s_passes_pool_keys():
    allowlist = _k8s_allowlist()
    for key in POOL_KEYS:
        assert key in allowlist, key


def test_pool_keys_are_known_with_image_defaults():
    known = _known_keys()
    for key, default in POOL_KEYS.items():
        assert key in known, key
        assert known[key].get("default") == default, key


def test_edge_assumes_the_same_pool_size_as_php_fpm():
    env = _compose_web_env()
    match = re.fullmatch(r"\$\{PM_MAX_CHILDREN:-(\d+)\}", env["PM_MAX_CHILDREN"])
    assert match and int(match.group(1)) == DEFAULT_PM_MAX_CHILDREN
