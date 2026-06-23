"""Structural test for `canasta storage setup efs`.

EFS setup needs a live AWS account + Kubernetes cluster, so it can't run in
CI as an integration test. These assertions lock in the playbook's contract:
it must validate the cluster up front, install the EFS CSI driver, create a
StorageClass backed by the EFS provisioner, and persist the chosen default
StorageClass to the controller registry (with delegate_to: localhost, per
the registry-lives-on-the-controller rule).
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
EFS_PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "storage_setup_efs.yml")


def _tasks():
    with open(EFS_PLAYBOOK) as f:
        return yaml.safe_load(f)


def _find(module):
    return [t for t in _tasks() if module in t]


def test_validates_kubernetes_prerequisites_first():
    tasks = _tasks()
    role_tasks = [t for t in tasks if "ansible.builtin.include_role" in t]
    preflight = [
        t for t in role_tasks
        if t["ansible.builtin.include_role"].get("tasks_from") == "k8s_preflight.yml"
    ]
    assert preflight, "EFS setup must run k8s_preflight before touching the cluster"
    # Preflight must come before the CSI install / StorageClass creation.
    assert tasks.index(preflight[0]) < tasks.index(_find("kubernetes.core.helm")[0])


def test_installs_efs_csi_driver():
    repos = _find("kubernetes.core.helm_repository")
    assert any(
        "aws-efs-csi-driver" in str(t["kubernetes.core.helm_repository"].get("repo_url", ""))
        for t in repos
    ), "must add the aws-efs-csi-driver Helm repo"
    charts = _find("kubernetes.core.helm")
    assert any(
        "aws-efs-csi-driver" in str(t["kubernetes.core.helm"].get("chart_ref", ""))
        for t in charts
    ), "must install the aws-efs-csi-driver chart"


def test_creates_storageclass_with_efs_provisioner():
    k8s_tasks = _find("kubernetes.core.k8s")
    scs = [
        t for t in k8s_tasks
        if (t["kubernetes.core.k8s"].get("definition", {}) or {}).get("kind")
        == "StorageClass"
    ]
    assert scs, "must create a StorageClass"
    sc = scs[0]["kubernetes.core.k8s"]["definition"]
    assert sc["provisioner"] == "efs.csi.aws.com", (
        "the StorageClass must use the EFS CSI provisioner"
    )


def test_persists_default_storageclass_to_controller_registry():
    reg = _find("canasta_registry")
    saves = [
        t for t in reg
        if t["canasta_registry"].get("state") == "set_setting"
        and t["canasta_registry"].get("setting_key") == "defaultStorageClass"
    ]
    assert saves, "must persist defaultStorageClass to the registry"
    # The registry lives on the controller — the write must be delegated.
    assert saves[0].get("delegate_to") == "localhost", (
        "canasta_registry writes must use delegate_to: localhost"
    )
