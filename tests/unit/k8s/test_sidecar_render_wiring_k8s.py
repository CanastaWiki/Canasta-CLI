"""Structural guards for wiring the sidecar render into the Kubernetes start.

start renders sidecars for Kubernetes, builds and pushes
build: sidecars to the in-cluster registry before the Helm rollout, and the
Helm deploy passes the rendered values to a chart that emits every resource
a sidecar needs. The post-stop scale-up is generalized to `--all` so a
Helm-managed sidecar is restored rather than left scaled to zero.
"""

import os

from test_sidecar_render_wiring import ROOT, START, _text

HELM = os.path.join(ROOT, "roles", "orchestrator", "tasks", "helm_deploy.yml")
SCALE_UP = os.path.join(
    ROOT, "roles", "orchestrator", "tasks", "k8s_scale_up_after_stop.yml")
CHART = os.path.join(ROOT, "roles", "orchestrator", "files", "helm", "canasta")
TPL = os.path.join(CHART, "templates", "sidecars.yaml")
VALUES = os.path.join(CHART, "values.yaml")

I = "{{ inspect_command | default('docker') }}"


def test_start_renders_sidecars_for_kubernetes():
    body = _text(START)
    assert "orchestrator: kubernetes" in body


def test_scale_up_is_generalized_to_all():
    body = _text(SCALE_UP)
    # The post-stop restore must scale `deployment --all` (covering sidecars),
    # not a fixed core-only list, then restore the web count.
    assert "kubectl scale deployment --all --replicas=1" in body
    assert "Restore web replica count" in body
    # The old fixed-list restore must be gone.
    assert "canasta-{{ instance_id }}-caddy\n" not in body


def test_k8s_builds_sidecar_images_before_rollout():
    body = _text(START)
    # build: sidecars are built + pushed before the Helm rollout (k8s-only).
    assert "k8s_build_sidecars.yml" in body
    assert body.index("k8s_build_sidecars.yml") < body.index("helm_deploy.yml")


def test_build_task_pushes_to_in_cluster_registry():
    path = os.path.join(ROOT, "roles", "orchestrator", "tasks",
                        "k8s_build_sidecars.yml")
    body = _text(path)
    assert "k8s_ensure_registry.yml" in body
    assert "%s build" % I in body and "%s push" % I in body
    assert "localhost:5000/canasta-{{ instance_id }}-{{ item.name }}:local" in body


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
    assert "name: {{ $sc.name | quote }}" in body
