"""Structural test for `canasta storage list`.

Listing StorageClasses needs a live cluster, so it can't run in CI as an
integration test. These assertions lock in the playbook's contract: read the
canasta-configured default from the controller registry (delegate_to:
localhost), list the cluster's StorageClasses, and surface the kubectl
context so the operator can catch a wrong-cluster listing.
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
LIST_PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "storage_list.yml")


def _tasks():
    with open(LIST_PLAYBOOK) as f:
        return yaml.safe_load(f)


def _commands():
    cmds = []
    for t in _tasks():
        c = t.get("ansible.builtin.command") or t.get("command")
        if isinstance(c, dict):
            cmds.append(c.get("cmd", ""))
        elif isinstance(c, str):
            cmds.append(c)
    return cmds


def test_reads_default_storageclass_from_controller_registry():
    reg = [t for t in _tasks() if "canasta_registry" in t]
    reads = [
        t for t in reg
        if t["canasta_registry"].get("state") == "get_setting"
        and t["canasta_registry"].get("setting_key") == "defaultStorageClass"
    ]
    assert reads, "must read defaultStorageClass from the registry"
    assert reads[0].get("delegate_to") == "localhost", (
        "canasta_registry reads must use delegate_to: localhost"
    )


def test_lists_cluster_storageclasses():
    assert any("kubectl get storageclass" in c for c in _commands()), (
        "must list the cluster's StorageClasses"
    )


def test_surfaces_kubectl_context():
    # Surfacing the context is the operator's cue that the listing targets
    # the intended cluster (not, say, a laptop's Docker Desktop).
    assert any("kubectl config current-context" in c for c in _commands()), (
        "must surface the current kubectl context"
    )
