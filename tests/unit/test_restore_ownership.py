"""Guard for restore file-ownership normalization (Compose).

The Compose restore copies files to the host with `cp -a`, which preserves the
snapshot's numeric uids. On a cross-host restore those ids need not map to a
real user, so `.env` (mode 0600) came back unreadable by the CLI user and
aborted the restore before the DB import. The copy step must chown restored
top-level files back to the instance dir's owner. This locks that in.
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


def _copy_task():
    with open(COMPOSE) as f:
        tasks = list(_flatten(yaml.safe_load(f)))
    return next(t for t in tasks if t.get("name") == "Copy files from volume to host")


def test_copy_step_normalizes_ownership_to_instance_owner():
    cmd = _copy_task()["ansible.builtin.shell"]["cmd"]
    # Derives the target from the instance dir owner (not a hard-coded uid)...
    assert 'stat -c "%u:%g" /install' in cmd, (
        "restore must derive ownership from the instance dir, not assume a uid")
    # ...and chowns restored files to it.
    assert "chown" in cmd, (
        "restore must chown restored files or a cross-host restore leaves .env "
        "owned by an unmapped uid and unreadable by the CLI user")
