"""Structural guards for wiring the sidecar render into start/stop/helm.

These lock in the integration points that the renderer depends on but that a
unit test of the render logic can't see: the generated Compose layer is added
as an `-f` (before the user override), the k8s render feeds Helm values, and
the post-stop scale-up is generalized to `--all` so a Helm-managed sidecar is
actually restored (otherwise it stays scaled-to-0 — the bug this fixes).
"""

import os

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
START = os.path.join(ROOT, "roles", "orchestrator", "tasks", "start.yml")
STOP = os.path.join(ROOT, "roles", "orchestrator", "tasks", "stop.yml")
HELM = os.path.join(ROOT, "roles", "orchestrator", "tasks", "helm_deploy.yml")
CHART = os.path.join(ROOT, "roles", "orchestrator", "files", "helm", "canasta")
TPL = os.path.join(CHART, "templates", "sidecars.yaml")
VALUES = os.path.join(CHART, "values.yaml")


def _text(path):
    with open(path) as handle:
        return handle.read()


def test_start_renders_sidecars_for_both_orchestrators():
    body = _text(START)
    assert "canasta_render_sidecars" in body
    assert "orchestrator: compose" in body
    assert "orchestrator: kubernetes" in body


def test_compose_layer_added_before_user_override():
    body = _text(START)
    assert "docker-compose.sidecars.yml" in body
    # Sidecars layer must precede the override in the -f list so the
    # override wins a service-name clash.
    assert body.index("'docker-compose.sidecars.yml'], [])") < body.index(
        "'docker-compose.override.yml'], [])")


def test_stop_includes_sidecar_layer_for_teardown():
    body = _text(STOP)
    assert "docker-compose.sidecars.yml" in body


def test_scale_up_is_generalized_to_all():
    body = _text(START)
    # The post-stop restore must scale `deployment --all` (covering sidecars),
    # not a fixed core-only list, then restore the web count.
    assert "kubectl scale deployment --all --replicas=1" in body
    assert "Restore web replica count" in body
    # The old fixed-list restore must be gone.
    assert "canasta-{{ instance_id }}-caddy\n" not in body


def test_helm_deploy_passes_sidecar_values():
    body = _text(HELM)
    assert "values-sidecars.yaml" in body


def test_values_defaults_sidecars_empty():
    assert "sidecars: []" in _text(VALUES)


def test_chart_template_emits_all_resources_per_sidecar():
    body = _text(TPL)
    assert "range $sc := .Values.sidecars" in body
    for kind in ("kind: Deployment", "kind: Service",
                 "kind: PersistentVolumeClaim", "kind: ConfigMap"):
        assert kind in body
    # Replicas pinned (so helm reconciles), and a keep annotation on the PVC.
    assert "replicas: 1" in body
    assert "helm.sh/resource-policy: keep" in body
    # Service named for the sidecar (bare name) so the wiki reaches it by host.
    assert "name: {{ $sc.name }}" in body
