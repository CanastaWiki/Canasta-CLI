"""Guards for backup-schedule crontab de-duplication.

Re-scheduling an instance that was previously scheduled by a different Canasta
version / CLI flavor left the old crontab entry in place (different marker), so
the instance backed up twice and `schedule list` reported only the first
(stale) entry. `apply` must now sweep every prior entry for the instance before
writing the new one, and `list` must warn when duplicates exist.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
APPLY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_apply.yml")
LIST = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_list.yml")


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _names(path):
    return [t.get("name") for t in _tasks(path) if isinstance(t, dict)]


def _by_name(path, name):
    return next((t for t in _tasks(path) if t.get("name") == name), None)


LIVE = "Remove any prior backup schedule entries for this instance (live crontab)"
FILE = "Remove any prior backup schedule entries for this instance (host crontab file)"


def test_apply_sweeps_both_crontab_paths():
    for name in (LIVE, FILE):
        t = _by_name(APPLY, name)
        assert t is not None, "missing sweep task: %s" % name
        cmd = t["ansible.builtin.shell"]["cmd"]
        # Matches the same job fragment `list` uses (trailing space so
        # 'foo' != 'foo2') plus the Canasta marker comments.
        assert "backup create -i {{ instance_id }}" in cmd
        assert "canasta-backup-{{ instance_id }}" in cmd


def test_sweep_runs_before_the_write():
    names = _names(APPLY)
    assert names.index(LIVE) < names.index("Set cron schedule"), (
        "live-crontab sweep must run before the cron-module write")
    assert names.index(FILE) < names.index("Set cron schedule in the host crontab file"), (
        "file sweep must run before the blockinfile write")


def test_sweeps_are_gated_to_their_path():
    # live-crontab sweep on the module path; file sweep on the mounted-file path
    assert "not _sched_use_file" in str(_by_name(APPLY, LIVE).get("when"))
    assert _by_name(APPLY, FILE).get("when") == "_sched_use_file"


def test_list_warns_on_duplicate_entries():
    t = _by_name(LIST, "Warn about duplicate schedule entries")
    assert t is not None, "list must warn when multiple schedule entries exist"
    assert "_schedule_lines | length > 1" in str(t.get("when"))
