"""`gitops sync` triggers an Argo CD sync on Kubernetes and only prints a
message on Compose."""

import os

from test_gitops_backup_schedule_rematerialize import (
    REPO_ROOT,
    _includes,
    _tasks,
)


TRIGGER_SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "gitops_trigger_sync.yml"
)


def test_gitops_sync_is_a_compose_noop():
    tasks = _tasks(TRIGGER_SYNC)
    # On Compose, the only action is a debug message gated on the compose
    # orchestrator — no reconciler to trigger.
    debug_compose = [
        t for t in tasks
        if "ansible.builtin.debug" in t and t.get("when") == "not (instance_is_k8s | bool)"
    ]
    assert debug_compose, "gitops sync must be a no-op (debug only) on Compose"
    # The Argo CD sync is gated on the kubernetes orchestrator.
    argocd = [t for t in tasks if "k8s_argocd_sync.yml" in _includes(t)]
    assert argocd and argocd[0].get("when") == "instance_is_k8s | bool", (
        "gitops sync must dispatch to Argo CD sync only on Kubernetes"
    )
