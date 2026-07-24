"""Regression guard for #1150: a failed DB import must fail loudly with the
real mariadb error, never a silent success that leaves an empty database.

Two structural invariants keep the fix in place:

  * resilient_exec.yml (the launch-then-poll used by every `exec_long` command,
    including the import load) must set `failed_when: false` on the completion
    poll. Otherwise async_status aborts the poll on a completed job's non-zero
    rc — before "Report failure" runs — and on older ansible retries the
    deterministic failure until the retries drain. failed_when: false keeps the
    rc decision in "Report failure", so the load fails fast with the stderr and
    `rx_fail` is honored.

  * import_database.yml must source the DB root password from the db
    container's own $MYSQL_ROOT_PASSWORD env, not inline it on the command
    line. Inlining forces `exec_no_log`, which redacts "Report failure" — so
    the operator sees a censored message instead of the SQL error.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RESILIENT_EXEC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "resilient_exec.yml")
IMPORT_DB = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "import_database.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


class TestResilientExecFailsFast:
    def test_completion_poll_does_not_fail_on_nonzero_rc(self):
        tasks = _load(RESILIENT_EXEC)
        waits = [
            t for t in tasks
            if isinstance(t, dict)
            and ("ansible.builtin.async_status" in t or "async_status" in t)
            and "until" in t
        ]
        assert waits, (
            "resilient_exec.yml must poll completion with async_status + until")
        for t in waits:
            assert t.get("failed_when") is False, (
                "the completion poll must set `failed_when: false` so a "
                "completed job's non-zero rc reaches 'Report failure' instead "
                "of aborting the poll / retrying a deterministic failure "
                "(#1150): %r" % t.get("name"))

    def test_report_failure_task_present(self):
        tasks = _load(RESILIENT_EXEC)
        fails = [t for t in tasks if isinstance(t, dict)
                 and ("ansible.builtin.fail" in t or "fail" in t)]
        assert fails, (
            "resilient_exec.yml must keep a 'Report failure' task that surfaces "
            "the job's rc/stderr")


class TestImportSurfacesError:
    def _load_commands(self):
        """(task, exec_command) for every include of exec.yml that runs a
        mariadb command in import_database.yml."""
        out = []
        for t in _load(IMPORT_DB):
            if not isinstance(t, dict):
                continue
            v = t.get("vars") or {}
            cmd = v.get("exec_command", "")
            if "mariadb" in cmd:
                out.append((t, v, cmd))
        return out

    def test_password_not_inlined_on_command_line(self):
        cmds = self._load_commands()
        assert cmds, "import_database.yml must run mariadb commands"
        for t, _v, cmd in cmds:
            assert "import_db_password" not in cmd, (
                "the DB load must not inline import_db_password on the command "
                "line (forces no_log, which redacts the SQL error) (#1150): %r"
                % cmd)
            assert "-p'" not in cmd and "-p{{" not in cmd, (
                "the DB load must not pass -p<password> on the command line "
                "(#1150): %r" % cmd)

    def test_password_sourced_from_container_env(self):
        for _t, _v, cmd in self._load_commands():
            assert "MYSQL_ROOT_PASSWORD" in cmd, (
                "the DB load must source the password from the db container's "
                "$MYSQL_ROOT_PASSWORD env: %r" % cmd)

    def test_load_execs_not_no_log(self):
        for t, v, _cmd in self._load_commands():
            assert not v.get("exec_no_log"), (
                "the DB load must not set exec_no_log, so the mariadb stderr "
                "(the SQL error) surfaces on failure (#1150): %r" % t.get("name"))
