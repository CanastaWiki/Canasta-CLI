"""Regression test for the Compose backup-schedule purge flag.

The original bug: schedule_set.yml generated a host cron calling
`canasta backup purge --keep-within <dur>`, but `canasta backup purge`
did not accept `--keep-within` (it had a canasta-specific `--older-than`
instead). Every scheduled purge failed and snapshots accumulated even
though `create` succeeded.

The fix made `canasta backup purge` mirror restic's `forget` flags
(including `--keep-within`). These tests guard the invariant directly:
the flag the schedule generates must be one the purge command accepts.

Pure YAML-structure parsing, mirroring test_backup_schedule_k8s.py.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SCHEDULE_SET = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "schedule_set.yml",
)
COMMAND_DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _walk_tasks(tasks):
    """Yield every task dict, descending into block/rescue/always."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _compose_purge_flag():
    """The retention flag in the Compose `_cron_purge` template."""
    with open(SCHEDULE_SET) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
        if isinstance(sf, dict) and "_cron_purge" in sf:
            m = re.search(r"backup purge\b.*?(--[a-z-]+)", sf["_cron_purge"])
            assert m, "no purge flag found in _cron_purge template"
            return m.group(1)
    raise AssertionError("schedule_set.yml has no _cron_purge set_fact")


def _purge_accepted_flags():
    """Flags accepted by `canasta backup purge`, per command_definitions."""
    with open(COMMAND_DEFS) as f:
        defs = yaml.safe_load(f)
    for cmd in defs.get("commands", []):
        if cmd.get("name") == "backup_purge":
            return {
                "--" + p["name"].replace("_", "-")
                for p in cmd.get("parameters", [])
            }
    raise AssertionError("backup_purge not found in command_definitions.yml")


class TestComposeSchedulePurgeFlag:
    def test_schedule_purge_flag_is_accepted_by_purge(self):
        flag = _compose_purge_flag()
        accepted = _purge_accepted_flags()
        assert flag in accepted, (
            f"schedule generates `canasta backup purge {flag}` but purge "
            f"only accepts {sorted(accepted)}"
        )

    def test_schedule_uses_restic_native_keep_within(self):
        assert _compose_purge_flag() == "--keep-within"
