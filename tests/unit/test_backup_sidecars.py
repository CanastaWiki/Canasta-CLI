"""Backups capture the sidecars/ build contexts, and restores bring them back.

A sidecar declared with `build: sidecars/<name>` is built from that directory
on every start, so a snapshot that holds config/sidecars.yaml but not the
directory restores onto a new host as an instance that cannot start. Most
instances have no sidecars/ directory, so both directions must treat it as
optional, and a snapshot taken before it was captured must still restore.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CREATE = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "create.yml")
K8S_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")
COMPOSE_RESTORE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")
K8S_RESTORE = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml")


def _flatten(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            if key in t:
                yield from _flatten(t[key])


def _tasks(path):
    with open(path) as f:
        return list(_flatten(yaml.safe_load(f)))


def _task(path, name):
    task = next((t for t in _tasks(path) if t.get("name") == name), None)
    assert task is not None, "missing task: %s" % name
    return task


def _when(task):
    w = task.get("when", [])
    if isinstance(w, list):
        return " and ".join(str(x) for x in w)
    return str(w)


# --- Compose backup ---

def test_compose_backup_stages_sidecars_when_present():
    stat = _task(CREATE, "Check for optional files")
    assert "sidecars" in stat["loop"]
    add = _task(CREATE, "Add optional files to staging")
    assert "item.stat.exists" in _when(add), (
        "sidecars/ must be staged only when it exists, or the bind mount "
        "creates an empty directory on hosts that have none")


def test_compose_safety_backup_stages_sidecars_when_present():
    stat = _task(COMPOSE_RESTORE, "Check for optional files (safety)")
    assert "sidecars" in stat["loop"]
    add = _task(COMPOSE_RESTORE, "Add optional files to safety staging")
    assert "item.stat.exists" in _when(add)


# --- Kubernetes backup ---

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


# --- Compose restore ---

def test_compose_restore_round_trips_sidecars_generically():
    # sidecars/ is not a standard dir, so the generic loop restores it when
    # the snapshot has it and leaves the host's copy alone when it does not.
    cmd = _task(COMPOSE_RESTORE, "Copy files from volume to host")[
        "ansible.builtin.shell"]["cmd"]
    std = next(line for line in cmd.splitlines() if "std=" in line)
    assert "sidecars" not in std
    assert 'if [ -d "$e" ]; then rm -rf "/install/$name"; fi;' in cmd


def test_compose_single_wiki_restore_leaves_sidecars_alone():
    cmd = _task(COMPOSE_RESTORE, "Copy single wiki's files from volume to host")[
        "ansible.builtin.shell"]["cmd"]
    assert "sidecars" not in cmd


# --- Kubernetes restore ---

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
