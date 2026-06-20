"""Regression test: the Compose backup staging volume is created and removed
under the same name.

run_backup.yml creates `canasta-backup-<basename>`; destroy.yml must remove
that exact name. A mismatch (the historical bug) silently orphans the volume:
`docker volume rm` targets a name that never existed and the real volume leaks
across create/backup/delete cycles.
"""

import os
import re

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ORCHESTRATOR_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


def _tasks(filename):
    with open(os.path.join(ORCHESTRATOR_TASKS, filename)) as f:
        return yaml.safe_load(f)


def _walk(tasks):
    """Yield every task dict, descending into block/ blocks."""
    for task in tasks:
        yield task
        if isinstance(task.get("block"), list):
            yield from _walk(task["block"])


def _created_volume_name():
    """The volume name set_fact'd as _bvol in run_backup.yml."""
    for task in _walk(_tasks("run_backup.yml")):
        set_fact = task.get("ansible.builtin.set_fact") or task.get("set_fact")
        if set_fact and "_bvol" in set_fact:
            return set_fact["_bvol"].strip()
    raise AssertionError("no _bvol set_fact found in run_backup.yml")


def _removed_volume_name():
    """The volume name targeted by `docker volume rm` in destroy.yml."""
    for task in _walk(_tasks("destroy.yml")):
        cmd_mod = task.get("ansible.builtin.command") or task.get("command")
        cmd = cmd_mod.get("cmd", "") if isinstance(cmd_mod, dict) else ""
        m = re.search(r"docker volume rm\s+(.+)$", cmd)
        if m:
            return m.group(1).strip()
    raise AssertionError("no `docker volume rm` command found in destroy.yml")


def test_backup_staging_volume_names_match():
    assert _removed_volume_name() == _created_volume_name()


def test_backup_staging_volume_uses_canasta_prefix():
    assert _created_volume_name() == "canasta-backup-{{ instance_path | basename }}"
