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
COMPOSE_RESTORE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


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
