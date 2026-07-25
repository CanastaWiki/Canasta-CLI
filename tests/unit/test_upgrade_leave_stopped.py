"""Guard: canasta upgrade must not start an instance that was stopped.

The restart step runs stop->start whenever a restart is needed (image bump,
compose change, or build-from rebuild). For an instance that was stopped before
the upgrade that would start it — colliding on host ports with a co-located
running instance and aborting the run. The fix records the pre-upgrade running
state and gates the restart's start on it, leaving a stopped instance stopped
(its refreshed config/image apply on the next start).
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _iter_tasks(tasks):
    for t in tasks:
        yield t
        if isinstance(t, dict) and "block" in t:
            yield from _iter_tasks(t["block"])


def _find(tasks, name_substring):
    for t in _iter_tasks(tasks):
        if isinstance(t, dict) and name_substring in t.get("name", ""):
            return t
    return None


class TestUpgradeLeavesStoppedInstanceStopped:
    def test_probes_running_state(self):
        tasks = _load(UPGRADE_MAIN)
        probe = next(
            (t for t in _iter_tasks(tasks)
             if "check_running.yml" in str(
                 t.get("ansible.builtin.include_tasks", ""))),
            None,
        )
        assert probe is not None, (
            "upgrade must probe running state via check_running.yml so it can "
            "tell a stopped instance from a running one"
        )

    def test_records_pre_upgrade_running_state(self):
        record = _find(_load(UPGRADE_MAIN), "Record pre-upgrade running state")
        assert record is not None
        assert "_was_running" in str(record["ansible.builtin.set_fact"]), (
            "upgrade must record the pre-upgrade running state as _was_running"
        )

    def test_running_probe_precedes_restart(self):
        # The probe must capture state before the restart step consumes it.
        names = [t.get("name", "") for t in _iter_tasks(_load(UPGRADE_MAIN))
                 if isinstance(t, dict)]
        record_i = names.index("Record pre-upgrade running state")
        restart_i = names.index("Restart containers")
        assert record_i < restart_i, (
            "the running-state probe must run before the restart step"
        )

    def test_restart_start_gated_on_was_running(self):
        restart = _find(_load(UPGRADE_MAIN), "Restart containers")
        when = str(restart.get("when", ""))
        assert "_restart_needed" in when and "_was_running" in when, (
            "the restart (stop->start) must fire only when a restart is needed "
            "AND the instance was already running, so a stopped instance is "
            "never started by upgrade"
        )

    def test_stopped_instance_is_reported_and_left_stopped(self):
        # A distinct branch covers the restart-needed-but-stopped case, so the
        # refresh isn't silently reported as a no-op.
        tasks = _load(UPGRADE_MAIN)
        stopped_branch = next(
            (t for t in _iter_tasks(tasks)
             if isinstance(t, dict)
             and t.get("name") != "Restart containers"
             and "not (_was_running" in str(t.get("when", ""))
             and "_restart_needed" in str(t.get("when", ""))),
            None,
        )
        assert stopped_branch is not None, (
            "upgrade must report that a restart-needed but stopped instance was "
            "left stopped, rather than starting it or claiming it's up to date"
        )
