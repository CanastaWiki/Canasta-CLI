"""Structural guards for the Kubernetes restore path.

The k8s backup captures the images PVC (uploaded media), but restore used to
extract only config, .env, the DB, and Secrets — the media in the snapshot was
restored into the restore pod's emptyDir and discarded, so a restore (and any
clone into a fresh cluster) silently lost all uploads. The fix mounts the
images PVC into the restore pod so `restic restore --target /` lands media
directly in it. These tests lock that wiring; runtime behavior is covered by
the live k8s e2e.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RESTORE = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml")


def _tasks():
    with open(RESTORE) as f:
        return yaml.safe_load(f)


def test_restore_pod_mounts_the_images_pvc():
    tasks = _tasks()
    mounts_task = next(
        t for t in tasks if t.get("name") == "Build restore pod volume mounts")
    mounts = mounts_task["ansible.builtin.set_fact"]["_restore_volume_mounts"]
    assert any(m.get("name") == "images"
               and m.get("mountPath") == "/currentsnapshot/images"
               for m in mounts), (
        "restore pod must mount the images PVC at the snapshot's images path, "
        "or restic restores media into an emptyDir and it is discarded")


def test_restore_declares_the_images_pvc_volume():
    tasks = _tasks()
    vols_task = next(
        t for t in tasks if t.get("name") == "Build restore pod volumes")
    vols = vols_task["ansible.builtin.set_fact"]["_restore_volumes"]
    images = next((v for v in vols if v.get("name") == "images"), None)
    assert images is not None, "restore pod is missing the images PVC volume"
    assert images["persistentVolumeClaim"]["claimName"] == \
        "canasta-{{ instance_id }}-images"
