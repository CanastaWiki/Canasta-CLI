"""An interrupted restore must leave a record, and be visible carrying one.

`canasta backup restore` mutates an instance in stages — config files, then
databases, then a restart, then the backup schedule. The rescue that explains
a failed restart only fires when the restart *task* fails; a killed process
never reaches it. Two restores died mid-task on a dropped SSH connection,
leaving instances running on half-applied config that `canasta list` reported
as healthy.

The marker's presence is the record: a restore that finishes removes it.
"""
import os
import re
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers, info  # noqa: E402

RESTORE = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "restore.yml")
RESTORE_INSTANCE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")
RESTORE_K8S = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_restore_instance.yml")
MARKER_TASKS = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "_restore_marker.yml")
MARKER_VARS = os.path.join(REPO_ROOT, "vars", "restore_marker.yml")
FINISH = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "finish_restore.yml")
START = os.path.join(
    REPO_ROOT, "roles", "instance_lifecycle", "tasks", "start.yml")
RESTART = os.path.join(
    REPO_ROOT, "roles", "instance_lifecycle", "tasks", "restart.yml")


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _tasks(path):
    with open(path) as fh:
        return list(_walk(yaml.safe_load(fh)))


def _named(path, name):
    for task in _tasks(path):
        if (task.get("name") or "") == name:
            return task
    return None


def _index(path, name):
    for i, task in enumerate(_tasks(path)):
        if (task.get("name") or "") == name:
            return i
    return -1


def _phases(path):
    return [t["vars"]["restore_marker_phase"]
            for t in _tasks(path)
            if "restore_marker_phase" in (t.get("vars") or {})]


class TestTheNameIsSharedWithThePythonSide:
    def test_ansible_and_the_cli_agree_on_the_filename(self):
        # _helpers.py cannot read Ansible vars, so the two are separate
        # definitions of one thing.
        with open(MARKER_VARS) as fh:
            assert yaml.safe_load(fh)["canasta_restore_marker"] == \
                _helpers.RESTORE_MARKER

    def test_it_sits_outside_the_directories_a_restore_replaces(self):
        # config/ is removed and recopied wholesale, so a marker there would
        # be destroyed by the very operation it is tracking.
        assert "/" not in _helpers.RESTORE_MARKER
        assert _helpers.RESTORE_MARKER.startswith(".")


class TestTheMarkerTracksTheRestore:
    def test_it_is_written_before_anything_is_mutated(self):
        assert (_index(RESTORE, "Mark the restore as in flight")
                < _index(RESTORE, "Restore from snapshot"))

    def test_every_phase_boundary_is_recorded(self):
        recorded = set(_phases(RESTORE) + _phases(RESTORE_INSTANCE))
        for phase in ("starting", "files", "databases", "restart",
                      "restart-failed", "backup-schedule"):
            assert phase in recorded, "phase '%s' is not recorded" % phase

    def test_it_is_cleared_only_after_the_last_mutating_step(self):
        assert (_index(FINISH, "Clear the restore marker")
                > _index(FINISH, "Re-materialize the backup schedule from restored state"))
        task = _named(FINISH, "Clear the restore marker")
        assert task["vars"]["restore_marker_clear"] is True

    def test_the_restore_finishes_through_the_shared_steps(self):
        assert (_index(RESTORE, "Mark the backup-schedule phase")
                < _index(RESTORE, "Finish the restore"))
        task = _named(RESTORE, "Finish the restore")
        assert task["ansible.builtin.include_tasks"] == "finish_restore.yml"
        assert task["vars"]["finish_restore_quiet"] is True

    def test_a_failed_restart_leaves_the_marker_in_place(self):
        # Restored but not running, and the backup schedule never
        # re-materialized: that is not a finished restore.
        task = _named(RESTORE, "Mark the restart as the phase that failed")
        assert task, "the rescue must record where it stopped"
        assert task["vars"]["restore_marker_phase"] == "restart-failed"

    def test_the_record_names_the_snapshot_and_the_target(self):
        content = str(_named(MARKER_TASKS, "Record the restore phase")
                      ["ansible.builtin.copy"]["content"])
        for field in ("_resolved_snapshot", "wiki", "restore_marker_phase",
                      "_restore_started_at"):
            assert field in content


def _finish_due_pattern():
    task = _named(FINISH, "Decide whether a restore is waiting to finish")
    expr = task["ansible.builtin.set_fact"]["_finish_restore_due"]
    return re.search(r"search\('(.*)'\)", expr).group(1)


def _marker(phase):
    return ("# An interrupted 'canasta backup restore'.\n"
            "snapshot: abc123\nwiki: (all wikis)\nphase: %s\n"
            "started: 2026-10-19T12:00:00Z\n" % phase)


class TestAFailedRestartIsFinishedByTheNextStart:
    """A restore whose restart fails has its data in place but never runs
    its last steps; the next successful start or restart runs them."""

    def test_start_and_restart_finish_it_after_the_containers_are_up(self):
        for path in (START, RESTART):
            finish = _named(path, "Finish a restore whose own restart failed")
            assert finish, path
            assert finish["ansible.builtin.include_tasks"].endswith(
                "/roles/backup/tasks/finish_restore.yml")
            assert (_index(path, "Start containers")
                    < _index(path, "Finish a restore whose own restart failed")), path

    def test_it_finishes_only_a_restore_whose_data_is_in_place(self):
        pattern = _finish_due_pattern()
        for phase in ("restart-failed", "backup-schedule"):
            assert re.search(pattern, _marker(phase)), phase
        # The restore's own restart runs restart.yml in this phase; the
        # restore finishes itself afterwards.
        for phase in ("starting", "files", "databases", "secrets", "restart"):
            assert not re.search(pattern, _marker(phase)), phase

    def test_every_step_waits_on_the_decision(self):
        for name in ("Re-materialize the backup schedule from restored state",
                     "Clear the restore marker",
                     "Remove the parked database password",
                     "Report the finished restore"):
            assert "_finish_restore_due | bool" in str(
                _named(FINISH, name)["when"]), name

    def test_a_missing_marker_is_not_an_error(self):
        assert _named(FINISH, "Read the restore marker")["failed_when"] is False

    def test_the_failed_restart_names_the_command_that_finishes_it(self):
        msg = str(_named(RESTORE, "Report a restore that succeeded with a failed restart")
                  ["ansible.builtin.fail"]["msg"])
        assert "canasta start" in msg
        assert "backup schedule" in msg


class TestTheKubernetesRestoreMarksItsPhases:
    """The Kubernetes restore is a separate task file, so the Compose
    marks never run there; without its own, an interrupted restore always
    reads 'phase: starting'."""

    def test_its_phases_are_marked_in_order(self):
        assert _phases(RESTORE_K8S) == ["files", "databases", "secrets"]

    def test_each_phase_is_marked_before_its_work(self):
        for mark, work in (
                ("Mark the files phase", "Create restore pod"),
                ("Mark the files phase", "Copy restored config to host"),
                ("Mark the databases phase", "Import each DB dump"),
                ("Mark the secrets phase",
                 "Restore canasta-managed K8s Secrets from snapshot")):
            assert _index(RESTORE_K8S, mark) >= 0, mark
            assert _index(RESTORE_K8S, mark) < _index(RESTORE_K8S, work), \
                (mark, work)

    def test_each_phase_follows_the_previous_ones_work(self):
        assert (_index(RESTORE_K8S, "Restore single wiki public assets to host")
                < _index(RESTORE_K8S, "Mark the databases phase"))
        assert (_index(RESTORE_K8S, "Import each DB dump")
                < _index(RESTORE_K8S, "Mark the secrets phase"))

    def test_the_marks_use_the_marker_tasks(self):
        for phase in ("databases", "secrets"):
            task = _named(RESTORE_K8S, "Mark the %s phase" % phase)
            assert task["ansible.builtin.include_tasks"].strip().endswith(
                "/roles/backup/tasks/_restore_marker.yml")
            assert task["vars"]["restore_marker_phase"] == phase

    def test_the_secrets_phase_is_marked_only_when_there_are_secrets(self):
        task = _named(RESTORE_K8S, "Mark the secrets phase")
        assert task["when"] == "_restore_secrets_filename != ''"


class TestAnInterruptedRestoreIsReported:
    def test_the_next_restore_says_what_it_is_resuming_from(self):
        task = _named(RESTORE, "Report the interrupted restore this one is resuming from")
        assert task, "the operator had to reconstruct this from timestamps"
        assert "_restore_prior_marker.content" in str(task["when"])

    def test_a_missing_marker_is_not_an_error(self):
        # The common case is no marker at all.
        assert _named(RESTORE, "Check for an interrupted restore")["failed_when"] is False


class TestListAndStatusSurfaceIt:
    def test_list_flags_the_instance(self):
        detail = _helpers._make_detail(
            "demo", "h", "/p", "compose", "RUNNING", [], True)
        assert "RESTORE INTERRUPTED" in detail["status"]
        assert "RUNNING" in detail["status"]

    def test_an_uninterrupted_instance_reads_exactly_as_before(self):
        detail = _helpers._make_detail("demo", "h", "/p", "compose", "RUNNING", [])
        assert detail["status"] == "RUNNING"

    def test_the_remote_gather_costs_no_extra_ssh_round_trip(self):
        # list probes every instance concurrently; a second connection per
        # instance is what the batched script exists to avoid.
        import inspect
        src = inspect.getsource(_helpers._gather_instance_info)
        assert src.count("_ssh_run(") == 1
        assert "RESTORE_INTERRUPTED" in src

    def test_status_prints_the_marker_itself(self, capsys):
        info._print_restore_marker(
            "# comment\nsnapshot: abc123\nphase: databases")
        out = capsys.readouterr().out
        assert "INTERRUPTED" in out
        assert "snapshot: abc123" in out
        assert "phase: databases" in out
        assert "# comment" not in out
        assert "canasta backup restore" in out, "the message must name the way out"

    def test_status_says_start_finishes_a_restore_whose_data_is_in_place(self, capsys):
        info._print_restore_marker(_marker("restart-failed"))
        out = capsys.readouterr().out
        assert "UNFINISHED" in out
        assert "half-applied" not in out
        assert "canasta start" in out
        assert "canasta backup restore" not in out

    def test_status_says_nothing_without_a_marker(self, capsys):
        info._print_restore_marker(None)
        assert capsys.readouterr().out == ""

    def test_reading_a_marker_needs_no_path(self):
        assert _helpers._read_restore_marker("", "localhost") is None

    def test_reading_an_absent_marker_is_not_an_error(self, tmp_path):
        assert _helpers._read_restore_marker(str(tmp_path), "localhost") is None

    def test_reading_a_present_marker_returns_it(self, tmp_path):
        (tmp_path / _helpers.RESTORE_MARKER).write_text("phase: files\n")
        assert _helpers._read_restore_marker(
            str(tmp_path), "localhost") == "phase: files"


class TestTheConnectionIsKeptAlive:
    def test_ssh_sends_keepalives(self):
        # Neither ssh nor sshd does by default, and a restore can spend tens
        # of minutes inside one task with no traffic on the connection.
        with open(os.path.join(REPO_ROOT, "ansible.cfg")) as fh:
            cfg = fh.read()
        assert "ServerAliveInterval=30" in cfg
        assert "ServerAliveCountMax=" in cfg
