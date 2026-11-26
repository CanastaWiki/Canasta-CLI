"""PHP-FPM pool settings in .env reach the Kubernetes web pods through the
pod environment allowlist in k8s_sync_config.yml."""

from test_php_fpm_pool_env import POOL_KEYS, _load


def _k8s_allowlist():
    tasks = _load("roles", "orchestrator", "tasks", "k8s_sync_config.yml")
    for task in tasks:
        facts = task.get("ansible.builtin.set_fact", {})
        if "_k8s_pod_env_allowlist" in facts:
            return facts["_k8s_pod_env_allowlist"]
    raise AssertionError("no _k8s_pod_env_allowlist in k8s_sync_config.yml")


def test_k8s_passes_pool_keys():
    allowlist = _k8s_allowlist()
    for key in POOL_KEYS:
        assert key in allowlist, key
