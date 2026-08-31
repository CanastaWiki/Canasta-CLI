"""An interrupted restore must not lose the destination's database password.

The restore reads MYSQL_PASSWORD into a fact, lets the copy replace .env with
the snapshot's, then writes the value back. A process killed in that window
leaves .env holding the *source's* password. MariaDB only applies the root
password when the data directory is first initialized, so the volume keeps the
destination's and nothing on disk still holds it.

Worse, the next restore reads that same .env as the value to preserve, so it
faithfully preserves the source's password and a successful-looking restore
makes the loss permanent.

The fix is to park the value on disk for exactly that span, where the copy
cannot reach it, and to finish the interrupted write before reading .env.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESTORE = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "restore.yml")
MARKER_VARS = os.path.join(REPO_ROOT, "vars", "restore_marker.yml")


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _tasks():
    with open(RESTORE) as fh:
        return list(_walk(yaml.safe_load(fh)))


def _named(name):
    for task in _tasks():
        if (task.get("name") or "") == name:
            return task
    return None


def _index(name):
    for i, task in enumerate(_tasks()):
        if (task.get("name") or "") == name:
            return i
    return -1


def _when(task):
    cond = task.get("when", [])
    return [str(c) for c in (cond if isinstance(cond, list) else [cond])]


class TestTheValueIsParkedForTheWindow:
    def test_it_is_parked_before_the_restore_can_replace_env(self):
        assert (_index("Park this host's database password for the span .env is the snapshot's")
                < _index("Restore from snapshot")), (
            "parked after the copy, it would not survive the window it exists for"
        )

    def test_it_is_parked_outside_what_the_restore_replaces(self):
        with open(MARKER_VARS) as fh:
            stash = yaml.safe_load(fh)["canasta_restore_dbpass_stash"]
        # config/ is removed and recopied wholesale; a name the snapshot
        # carries would be overwritten by the copy itself.
        assert "/" not in stash
        assert stash.startswith(".")

    def test_it_is_owner_only(self):
        task = _named("Park this host's database password for the span .env is the snapshot's")
        assert task["ansible.builtin.copy"]["mode"] == "0600"

    def test_parking_it_is_not_logged(self):
        for name in ("Park this host's database password for the span .env is the snapshot's",
                     "Read the parked database password",
                     "Put the parked database password back in .env"):
            assert _named(name).get("no_log") is True, name

    def test_a_single_wiki_restore_parks_nothing(self):
        # -w never replaces .env, so there is no window to cover.
        conds = _when(_named(
            "Park this host's database password for the span .env is the snapshot's"))
        assert any("wiki is not defined" in c for c in conds)

    def test_an_absent_password_is_not_parked(self):
        # canasta_env read returns "" for a key that is not there; parking
        # that would later blank the key rather than leave it alone.
        conds = _when(_named(
            "Park this host's database password for the span .env is the snapshot's"))
        assert any("default('', true)) != ''" in c for c in conds)

    def test_it_is_removed_only_after_a_restore_completes(self):
        assert (_index("Remove the parked database password")
                > _index("Re-materialize the backup schedule from restored state"))


class TestTheInterruptedWriteIsFinished:
    def test_the_parked_value_goes_back_before_env_is_read(self):
        # Otherwise the next restore preserves the source's password and
        # makes the loss permanent.
        assert (_index("Put the parked database password back in .env")
                < _index("Save current DB password before restore"))

    def test_it_only_runs_when_a_restore_was_interrupted(self):
        # A completed restore removes the stash, so its presence is the
        # signal — nothing happens on the common path.
        conds = _when(_named("Complete an unfinished restore's database-password write"))
        assert any("_restore_dbpass_stash_stat.stat.exists" in c for c in conds)

    def test_an_empty_stash_does_not_blank_the_key(self):
        conds = _when(_named("Put the parked database password back in .env"))
        assert any("!= ''" in c for c in conds)

    def test_the_recovery_is_reported(self):
        task = _named("Report the recovered database password")
        assert task, "silently rewriting a credential is its own surprise"
        msg = str(task["ansible.builtin.debug"]["msg"])
        assert "canasta config set" in msg, (
            "a password set by hand since the interruption is overwritten "
            "here; the message must name the way to re-apply it"
        )
        # Names and instructions only — never the value.
        assert "_restore_dbpass_stash.content" not in msg


class TestItComposesWithTheRestoreMarker:
    def test_both_are_cleared_by_the_same_completed_restore(self):
        for name in ("Clear the restore marker on success",
                     "Remove the parked database password"):
            assert _named(name), name

    def test_the_marker_report_comes_first(self):
        # The marker explains what happened; the recovery says what was done
        # about it.
        assert (_index("Report the interrupted restore this one is resuming from")
                < _index("Report the recovered database password"))
