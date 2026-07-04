"""Structural guards for the Kubernetes restore path.

The k8s backup captures config, .env, the DB, Secrets, uploaded media (images
PVC), and the extensions/skins/public_assets dirs — but restore used to bring
back only config, .env, the DB, and Secrets. Media in the snapshot was
restored into the restore pod's emptyDir and discarded, and the user dirs were
never restored at all, so a k8s restore (and any clone into a fresh cluster)
silently lost uploads and custom extensions/skins/public_assets. The fix mounts
the images PVC into the restore pod (restic lands media directly) and restores
the user dirs to the host (the post-restore restart's k8s_sync_user_dirs
mirrors them into their PVCs). These tests lock that wiring — including a
parity guard against backup capturing something restore drops; runtime
behavior is covered by the live k8s e2e.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RESTORE = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml")
BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")


def _tasks():
    with open(RESTORE) as f:
        return yaml.safe_load(f)


def _text(path):
    with open(path) as f:
        return f.read()


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


def test_restore_brings_back_user_dirs_to_host():
    tasks = _tasks()
    step = next((t for t in tasks
                 if t.get("name")
                 == "Restore extensions/skins/public_assets to host"), None)
    assert step is not None, (
        "restore must bring extensions/skins/public_assets back to the host "
        "(the post-restore restart then syncs them into their PVCs)")
    assert set(step["loop"]) == {"extensions", "skins", "public_assets"}


def test_restore_covers_every_dir_the_backup_captures():
    # Parity guard: every directory the k8s backup mounts under
    # /currentsnapshot must be restored by restore_k8s. This is the class of
    # bug that silently dropped images + extensions/skins/public_assets on
    # restore — the backup saved them, but restore never brought them back.
    captured = set(re.findall(r"/currentsnapshot/([A-Za-z_]+)", _text(BACKUP)))
    captured &= {"config", "images", "extensions", "skins", "public_assets"}
    # Sanity: the backup really does capture the media/user dirs.
    assert {"images", "extensions", "skins", "public_assets"} <= captured
    # Check the PARSED tasks (comments stripped) so a dir mentioned only in a
    # comment cannot satisfy the guard — it must be in real restore logic.
    restore_logic = yaml.safe_dump(_tasks())
    missing = [d for d in captured if d not in restore_logic]
    assert not missing, (
        "k8s backup captures %s but restore_k8s never restores %s — restore "
        "would silently drop it (the images/extensions/skins/public_assets "
        "restore gap). Every captured dir must be restored." % (captured, missing))
