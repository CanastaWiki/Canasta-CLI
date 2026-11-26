"""`canasta upgrade` on Kubernetes reconciles caddy.image in values.yaml and
treats that write as the signal that the managed Caddy image changed."""

import os

from test_upgrade_caddy_image import CHECK, REPO_ROOT, _load, _named


RECONCILE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_reconcile_caddy.yml")


def test_kubernetes_runs_the_deploy_reconcile_and_reads_its_write():
    tasks = _load(CHECK)
    k8s = _named(tasks, "Check the Caddy image (Kubernetes)")
    assert k8s["when"] == "instance_is_k8s | bool"
    include = _named(k8s["block"], "Reconcile the caddy image in values.yaml")
    assert include["ansible.builtin.include_role"] == {
        "name": "orchestrator", "tasks_from": "k8s_reconcile_caddy.yml"}
    note = _named(k8s["block"], "Note whether the Caddy image changes (Kubernetes)")
    assert note["ansible.builtin.set_fact"]["_upgrade_caddy_image_changed"] == (
        "{{ _reconcile_caddy_written is changed }}")
    write = _named(_load(RECONCILE), "Write reconciled values.yaml")
    assert write["register"] == "_reconcile_caddy_written"
