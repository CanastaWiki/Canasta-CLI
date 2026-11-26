"""Kubernetes backups capture the sidecars/ build contexts, and restores bring them back.

The backup pod mounts sidecars/ read-only at the snapshot path only when the
directory exists, since a missing hostPath of type Directory fails the pod.
A full restore replaces the host's sidecars/ only when the snapshot holds
one, and a single-wiki restore leaves it alone.
"""

import os

from test_backup_sidecars import REPO_ROOT, _task, _when


K8S_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")


K8S_RESTORE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_restore_instance.yml")


def test_k8s_backup_mounts_sidecars_only_when_present():
    stat = _task(K8S_BACKUP, "Check for a sidecars directory")
    assert stat["ansible.builtin.stat"]["path"] == \
        "{{ instance_path }}/sidecars"
    assert "when" not in stat, (
        "the stat is registered once, unconditionally")
    for name in ("Add sidecars volume mount", "Add sidecars hostPath volume"):
        assert "_restic_sidecars_dir.stat.isdir" in _when(
            _task(K8S_BACKUP, name)), (
            "%s must be skipped without a sidecars/ directory: a missing "
            "hostPath of type Directory fails the backup pod" % name)


def test_k8s_backup_sidecars_mount_is_read_only_at_the_snapshot_path():
    mount = _task(K8S_BACKUP, "Add sidecars volume mount")
    expr = mount["ansible.builtin.set_fact"]["_restic_volume_mounts"]
    assert "'/currentsnapshot/sidecars'" in expr
    assert "'readOnly': true" in expr
    vol = _task(K8S_BACKUP, "Add sidecars hostPath volume")
    expr = vol["ansible.builtin.set_fact"]["_restic_volumes"]
    assert "instance_path ~ '/sidecars'" in expr
    assert "'type': 'Directory'" in expr


def test_k8s_restore_replaces_sidecars_only_when_in_snapshot():
    task = _task(K8S_RESTORE, "Restore sidecar build contexts to host")
    assert "wiki is not defined" in _when(task), (
        "sidecars/ is instance-level; single-wiki restore must not touch it")
    cmd = task["ansible.builtin.shell"]["cmd"]
    guard = "test -d /currentsnapshot/sidecars; then"
    assert guard in cmd
    assert cmd.index(guard) < cmd.index('rm -rf "{{ instance_path }}/sidecars"'), (
        "the host's sidecars/ must be cleared only when the snapshot holds "
        "one, so an older snapshot leaves it in place")
    assert cmd.rstrip().endswith("fi")


def test_k8s_single_wiki_include_filter_excludes_sidecars():
    inc = _task(K8S_RESTORE,
                "Build restic include filters for single-wiki restore")
    assert "sidecars" not in inc["ansible.builtin.set_fact"]["_restore_include"]
