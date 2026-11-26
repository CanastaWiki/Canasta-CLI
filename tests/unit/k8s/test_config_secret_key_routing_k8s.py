"""On Kubernetes the app Secret built from config/secrets.env is replaced on
every sync, so a key removed with `config unset --secret` leaves the cluster
too."""

import os

from test_config_secret_key_routing import REPO_ROOT, _index, _load


K8S_SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_sync_config.yml"
)


def test_k8s_app_secret_is_replaced_not_patched():
    """A patched Secret keeps keys removed from secrets.env, so the pod goes
    on reading a credential the operator unset."""
    tasks = _load(K8S_SYNC)
    apply = tasks[_index(tasks, "Apply app secrets Secret")]["kubernetes.core.k8s"]
    assert apply.get("force") is True, (
        "the app Secret is derived from config/secrets.env and must be "
        "replaced, or `config unset --secret` leaves the key in the cluster")
