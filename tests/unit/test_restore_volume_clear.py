"""Guard: the Compose restore clears its staging volume before restoring.

The safety backup stages the current instance into the same
`canasta-backup-<id>` volume, and `restic restore` does not prune its target.
Without clearing the volume first, files present now but absent from the
snapshot leak through the restore. This test locks in the clear step and its
ordering (before the restic restore that reads the volume).
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


def _flatten(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            if key in t:
                yield from _flatten(t[key])


def _names_in_order():
    with open(COMPOSE) as f:
        return [t.get("name") for t in _flatten(yaml.safe_load(f))]


def test_volume_cleared_before_restore():
    names = _names_in_order()
    assert "Clear backup volume before restore" in names, (
        "restore must clear the staging volume or post-snapshot files leak")
    assert names.index("Clear backup volume before restore") < \
        names.index("Restore snapshot into backup volume"), (
        "the clear must run before the restic restore that reads the volume")
