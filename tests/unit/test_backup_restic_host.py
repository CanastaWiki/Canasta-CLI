"""Regression tests: backups must pass a stable restic --host.

Without --host, restic records each snapshot's host as the ephemeral
restic container's random id (different every run), so it never finds a
parent ("no parent snapshot found, will read all files") and
`forget --group-by host,paths` puts each snapshot in its own group,
defeating incremental backups and retention grouping.

create.yml feeds both the Compose and K8s on-demand backups (and the
Compose scheduled backup, which runs `canasta backup create`); the K8s
CronJob builds its own restic command in schedule_set.yml. Both must pin
--host to the instance id (stable within each instance's own repo).

Pure YAML-structure parsing, mirroring test_backup_schedule_compose.py.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CREATE_YML = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "create.yml",
)

STABLE_HOST = "{{ instance_id }}"


def _walk_tasks(tasks):
    """Yield every task dict, descending into block/rescue/always."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _create_backup_args():
    with open(CREATE_YML) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        if task.get("name") == "Create backup snapshot":
            return task.get("vars", {}).get("backup_args", [])
    raise AssertionError("create.yml has no 'Create backup snapshot' task")


class TestCreateBackupHost:
    def test_passes_host(self):
        args = _create_backup_args()
        assert "--host" in args, (
            "create.yml backup_args must pass --host for a stable restic "
            "host; got %r" % args
        )

    def test_host_is_instance_id(self):
        args = _create_backup_args()
        assert args[args.index("--host") + 1] == STABLE_HOST

    def test_host_precedes_backup_subcommand(self):
        args = _create_backup_args()
        assert args.index("--host") < args.index("backup")
