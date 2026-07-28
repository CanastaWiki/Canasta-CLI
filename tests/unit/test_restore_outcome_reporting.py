"""A restore that exits non-zero must say whether the data landed.

The snapshot's files and databases are both restored by
`restore_instance.yml`; the instance restart happens after it. So a
failure in the restart means "restored but not running" — the data is
already in place and re-running the restore is the wrong response.

Before this, both outcomes surfaced as a bare non-zero exit, so an
operator seeing a failed restore would reasonably assume nothing had
happened and retry, or worse, reach for an older snapshot.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
RESTORE = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "restore.yml")
RESTORE_INSTANCE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _named(tasks, needle):
    return [
        t for t in tasks
        if isinstance(t, dict) and needle.lower() in str(t.get("name", "")).lower()
    ]


class TestRestorePrecedesRestart:
    """The premise the message depends on: if the restart ever moved
    ahead of the data restore, the reassurance below would be false."""

    def test_restore_comes_before_the_restart(self):
        names = [
            str(t.get("name", "")) for t in _tasks(RESTORE)
            if isinstance(t, dict)
        ]
        restore_at = next(
            i for i, n in enumerate(names) if "Restore from snapshot" in n)
        restart_at = next(
            i for i, n in enumerate(names) if "Restart instance after" in n)
        assert restore_at < restart_at, (
            "the restart must follow the restore — the rescue message tells "
            "the operator their data is already in place"
        )

    def test_the_database_import_is_part_of_the_restore_step(self):
        # Not merely the file copy: the message claims databases too.
        with open(RESTORE_INSTANCE) as f:
            body = f.read()
        assert "Import each wiki database dump" in body, (
            "restore_instance.yml no longer imports databases; the rescue "
            "message in restore.yml claims it does"
        )


class TestFailedRestartIsReportedAsSuch:
    def test_the_restart_has_a_rescue(self):
        restart = _named(_tasks(RESTORE), "Restart instance after")
        assert restart, "no post-restore restart task found"
        assert "rescue" in restart[0], (
            "an unguarded restart failure is indistinguishable from a failed "
            "restore — wrap it so the outcome can be reported"
        )

    def test_the_message_says_the_data_is_restored(self):
        restart = _named(_tasks(RESTORE), "Restart instance after")[0]
        msg = " ".join(
            str(t.get("ansible.builtin.fail", {}).get("msg", ""))
            for t in restart["rescue"]
        ).lower()
        assert "restore completed" in msg, (
            "the rescue must state the restore itself succeeded"
        )
        assert "do not re-run the restore" in msg, (
            "the operator's instinct on a non-zero exit is to retry; the "
            "message has to head that off"
        )

    def test_the_message_names_the_recovery_command(self):
        restart = _named(_tasks(RESTORE), "Restart instance after")[0]
        msg = " ".join(
            str(t.get("ansible.builtin.fail", {}).get("msg", ""))
            for t in restart["rescue"]
        )
        assert "canasta start" in msg, (
            "say how to finish the job, not just what went wrong"
        )

    def test_it_still_exits_non_zero(self):
        # Restored-but-stopped is not success; the run must still fail.
        restart = _named(_tasks(RESTORE), "Restart instance after")[0]
        assert any(
            "ansible.builtin.fail" in t for t in restart["rescue"]
        ), "the rescue must re-fail — the instance is not running"
