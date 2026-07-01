"""Guard for 'canasta gitops sync' dispatch and Argo CD sync semantics.

gitops sync had no test. It dispatches per-orchestrator (Compose no-op,
Kubernetes triggers an Argo CD sync); the K8s path patches the instance's
Argo CD Application with a hard-refresh annotation and waits for the app to
report Synced + Healthy. These structural checks lock that in without a
live cluster.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TRIGGER_YML = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "gitops_trigger_sync.yml")
SYNC_YML = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_argocd_sync.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _when(task):
    w = task.get("when", [])
    return " ".join(w if isinstance(w, list) else [str(w)])


class TestSyncDispatch:
    def test_compose_is_a_gated_noop(self):
        tasks = _load(TRIGGER_YML)
        noop = [t for t in tasks
                if ("ansible.builtin.debug" in t or "debug" in t)
                and "compose" in _when(t)]
        assert noop, "Compose must be an explicit gated no-op"
        assert "== 'compose'" in _when(noop[0])

    def test_k8s_includes_argocd_sync(self):
        tasks = _load(TRIGGER_YML)
        inc = [t for t in tasks
               if str(t.get("ansible.builtin.include_tasks", ""))
               .endswith("k8s_argocd_sync.yml")]
        assert inc, "K8s must dispatch to k8s_argocd_sync.yml"
        cond = _when(inc[0])
        assert "kubernetes" in cond and "k8s" in cond


class TestArgocdSyncSemantics:
    def test_patches_instance_application_with_hard_refresh(self):
        tasks = _load(SYNC_YML)
        patches = [t.get("kubernetes.core.k8s") for t in tasks
                   if t.get("kubernetes.core.k8s")]
        assert patches, "must patch the Argo CD Application"
        patch = patches[0]
        assert patch.get("state") == "patched"
        assert patch.get("kind") == "Application"
        assert "canasta-" in patch.get("name", "")
        assert "instance_id" in patch.get("name", "")
        annotations = (patch.get("definition", {})
                       .get("metadata", {}).get("annotations", {}))
        assert annotations.get("argocd.argoproj.io/refresh") == "hard"

    def test_waits_for_synced_and_healthy(self):
        tasks = _load(SYNC_YML)
        waits = [t for t in tasks if t.get("kubernetes.core.k8s_info")]
        assert waits, "must poll the Application status"
        until = waits[0].get("until", [])
        until_text = " ".join(until if isinstance(until, list) else [until])
        assert "Synced" in until_text
        assert "Healthy" in until_text
