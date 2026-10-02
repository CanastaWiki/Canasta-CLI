"""Staged database dumps are removed even when the backup fails.

The dumps are full, unencrypted database copies written into the instance's
config/backup. Their cleanup used to be the step after the snapshot, so any
failure in between left them on disk, where a run that kept failing at the
same point kept a multi-gigabyte dump indefinitely. Both the backup create
and the pre-restore safety backup now remove them in an `always:`.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CREATE = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "create.yml")
RESTORE_INSTANCE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _block_holding(path, name):
    with open(path) as f:
        tasks = yaml.safe_load(f)
    for task in _walk(tasks):
        if any(t.get("name") == name for t in task.get("block") or []):
            return task
    raise AssertionError("no block directly holds %r" % name)


def _names(tasks):
    return [t.get("name") for t in _walk(tasks)]


def test_create_cleans_up_whether_or_not_the_snapshot_succeeds():
    block = _block_holding(CREATE, "Create backup snapshot")
    inside = _names(block["block"])
    assert "Stage database dumps for backup" in inside
    assert "Clean up backup staging directory" in _names(block.get("always"))
    assert "Clean up backup staging directory" not in inside


def test_safety_backup_cleans_up_whether_or_not_it_succeeds():
    block = _block_holding(RESTORE_INSTANCE, "Run safety backup with staged files")
    inside = _names(block["block"])
    assert "Dump each present wiki's database group for safety backup" in inside
    assert "Clean up safety DB dump directory" in _names(block.get("always"))
    assert "Clean up safety DB dump directory" not in inside
