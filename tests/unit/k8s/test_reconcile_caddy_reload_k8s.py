"""The reconcile Caddy reload stays off Kubernetes.

Kubernetes rolls the caddy pod off the ConfigMap checksum annotation, so the
reload gate excludes Kubernetes instances and the caddy Deployment keeps the
annotation that delivers a changed config.
"""

import os

from test_reconcile_caddy_reload import RELOAD_CADDY, REPO_ROOT, _load, _walk

CADDY_DEPLOYMENT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta",
    "templates", "deployment-caddy.yaml",
)


class TestReloadCaddyTask:
    def test_gate_requires_compose(self):
        gate = next(
            t for t in _walk(_load(RELOAD_CADDY))
            if "_reload_caddy" in (t.get("ansible.builtin.set_fact") or {})
        )
        expr = gate["ansible.builtin.set_fact"]["_reload_caddy"]
        assert "not (instance_is_k8s | bool)" in expr, (
            "K8s rolls the caddy pod off the ConfigMap checksum annotation; "
            "it must not be reloaded through the Compose path"
        )


class TestKubernetesStillRollsOnChecksum:
    """The reload is Compose-only because K8s already handles it. If that
    annotation ever goes away, K8s loses config delivery silently."""

    def test_caddy_deployment_has_a_config_checksum_annotation(self):
        with open(CADDY_DEPLOYMENT) as f:
            body = f.read()
        assert "checksum/caddy-config" in body, (
            "the caddy deployment must keep its config checksum annotation — "
            "it is what rolls the pod on a ConfigMap change, and the reason "
            "reload_caddy.yml is Compose-only"
        )
