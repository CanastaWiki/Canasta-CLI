"""PHP-FPM pool settings in .env reach the web container on both orchestrators.

The edge sizes its backend limits from PM_MAX_CHILDREN in .env, so PHP-FPM
has to read the same value, or the edge sends more requests than the pool
can take. A pool key .env does not set must keep the image default: on
Compose it is left out of the web environment, since podman-compose passes
`${KEY:-default}` for an unset key through as the literal string, which
stops PHP-FPM from starting.
"""

import os

import yaml

import canasta_render_web_env
from canasta_edge_render import DEFAULT_PM_MAX_CHILDREN
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
OVERLAY = "docker-compose.web-env.yml"

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


def _k8s_allowlist():
    tasks = _load("roles", "orchestrator", "tasks", "k8s_sync_config.yml")
    for task in tasks:
        facts = task.get("ansible.builtin.set_fact", {})
        if "_k8s_pod_env_allowlist" in facts:
            return facts["_k8s_pod_env_allowlist"]
    raise AssertionError("no _k8s_pod_env_allowlist in k8s_sync_config.yml")


def _render(inst, env, secret_keys=()):
    with open(os.path.join(inst, ".env"), "w") as f:
        f.write(env)
    if secret_keys:
        os.makedirs(os.path.join(inst, "config"), exist_ok=True)
        with open(os.path.join(inst, "config", "secret-keys-web"), "w") as f:
            f.write("".join(k + "\n" for k in secret_keys))
    result, failed, msg = run_module_with_params(
        canasta_render_web_env, {"instance_path": inst})
    assert not failed, msg
    path = os.path.join(inst, OVERLAY)
    if not os.path.exists(path):
        return result, None
    with open(path) as f:
        return result, yaml.safe_load(f)["services"]["web"]["environment"]


def test_base_compose_file_leaves_pool_keys_out():
    compose = _load(
        "roles", "orchestrator", "files", "compose", "docker-compose.yml")
    env = compose["services"]["web"]["environment"]
    names = {e.split("=", 1)[0] for e in env}
    assert not names & set(POOL_KEYS)


def test_module_knows_every_pool_key():
    assert set(canasta_render_web_env.POOL_KEYS) == set(POOL_KEYS)


def test_pool_keys_set_in_env_are_passed(tmp_dir):
    env = "".join("%s=1\n" % k for k in POOL_KEYS)
    _, overlay = _render(tmp_dir, "MW_SITE_SERVER=x\n" + env)
    assert overlay == ["%s=${%s}" % (k, k) for k in POOL_KEYS]


def test_unset_or_empty_pool_keys_are_left_out(tmp_dir):
    _, overlay = _render(
        tmp_dir,
        "PM_MAX_CHILDREN=12\nPM_START_SERVERS=\nPM_MIN_SPARE_SERVERS=''\n")
    assert overlay == ["PM_MAX_CHILDREN=${PM_MAX_CHILDREN}"]


def test_no_layer_when_no_pool_key_is_set(tmp_dir):
    result, overlay = _render(tmp_dir, "MW_SITE_SERVER=x\n")
    assert overlay is None and result["keys"] == []


def test_pool_keys_follow_web_secret_keys(tmp_dir):
    _, overlay = _render(
        tmp_dir, "MAPS_API_KEY=v\nPM_MAX_CHILDREN=12\n",
        secret_keys=["MAPS_API_KEY"])
    assert overlay == ["MAPS_API_KEY=${MAPS_API_KEY}",
                       "PM_MAX_CHILDREN=${PM_MAX_CHILDREN}"]


def test_unset_pool_key_is_not_reported_missing(tmp_dir):
    result, _ = _render(
        tmp_dir, "MAPS_API_KEY=v\n", secret_keys=["MAPS_API_KEY"])
    assert result["missing"] == []


def test_k8s_passes_pool_keys():
    allowlist = _k8s_allowlist()
    for key in POOL_KEYS:
        assert key in allowlist, key


def test_pool_keys_are_known_with_image_defaults():
    data = _load("roles", "config", "defaults", "main.yml")
    known = {k["name"]: k for k in data["canasta_known_keys"]}
    for key, default in POOL_KEYS.items():
        assert key in known, key
        assert known[key].get("default") == default, key


def test_edge_assumes_the_image_pool_size():
    assert DEFAULT_PM_MAX_CHILDREN == int(POOL_KEYS["PM_MAX_CHILDREN"])
