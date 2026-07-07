"""Guards for the MySQL 8.0 -> MariaDB upgrade migration.

This migration was ported from the retired Go CLI
(cmd/upgrade/mysql_to_mariadb.go). The first Ansible port regressed it in
three independent ways, each of which left an instance's DB unbootable or
its data mangled; these tests lock the Go-faithful behavior back in.

1. Detection keyed off /var/lib/mysql/.rocksdb, a MyRocks marker Canasta
   never creates and that is absent from every stock MySQL 8 datadir, so the
   probe always returned "not MySQL 8" and the migration was skipped. The
   definitive MySQL 8 marker is mysql.ibd, its system-schema InnoDB
   tablespace, which MariaDB never writes.

2. The dump ran against whatever container the instance compose happened to
   start, and the restart used that same (still MySQL 8) compose. The Go
   design instead dumps from a throwaway mysql:8.0 container it starts
   itself, so it is independent of the instance compose and can even recover
   an instance already swapped to MariaDB. The instance compose must be
   force-refreshed to the MariaDB image between clearing the volume and the
   restart.

3. The dump used mysqldump --all-databases, dragging MySQL 8's system schema
   (mysql/sys/*_schema, whose grant-table layout differs from MariaDB's) into
   the import. Only user databases must be dumped; MariaDB rebuilds its own
   system tables on the fresh datadir.

Hardening guards covered below: the internal-db gate, the volume-existence
check before the detection probe, the empty-database-list dump path, the
quote-filtered dump script, the non-root-user warning, and the gitignored
failure-path dump.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
MIGRATION = os.path.join(
    REPO_ROOT, "roles", "upgrade", "tasks", "migrations", "mysql_to_mariadb.yml"
)
GITIGNORE = os.path.join(
    REPO_ROOT, "roles", "gitops", "files", "gitignore.default"
)


def _load():
    with open(MIGRATION) as f:
        return yaml.safe_load(f)


def _iter_tasks(tasks):
    """Yield every task, descending into block/rescue/always wrappers."""
    for t in tasks:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            if key in t and isinstance(t[key], list):
                yield from _iter_tasks(t[key])


def _all_tasks():
    return list(_iter_tasks(_load()))


def _command_strings():
    """Every ansible.builtin.command cmd in the file, as a list of strings."""
    cmds = []
    for t in _all_tasks():
        cmd = t.get("ansible.builtin.command")
        if isinstance(cmd, dict):
            cmd = cmd.get("cmd", "")
        if isinstance(cmd, str) and cmd:
            cmds.append(cmd)
    return cmds


def _shell_text():
    """Command cmds plus per-task vars string values (the dump shell script
    lives in a vars block, referenced from the cmd)."""
    text = list(_command_strings())
    for t in _all_tasks():
        for v in (t.get("vars") or {}).values():
            if isinstance(v, str):
                text.append(v)
    return text


def _ordered_names():
    return [t.get("name", "") for t in _all_tasks()]


def _index_of(substr):
    for i, n in enumerate(_ordered_names()):
        if substr in n:
            return i
    raise AssertionError("no task name contains %r" % substr)


class TestDetectionMarker:
    def test_detects_by_ibd_not_rocksdb(self):
        cmds = " \n".join(_command_strings())
        assert "mysql.ibd" in cmds, (
            "detection must key off mysql.ibd (MySQL 8's system tablespace)"
        )
        assert ".rocksdb" not in cmds, (
            ".rocksdb is a MyRocks marker absent from stock MySQL 8 datadirs; "
            "keying off it makes the migration a silent no-op"
        )

    def test_detection_is_volume_read_only_not_service_exec(self):
        # A throwaway container reading the volume works whether the DB is up,
        # stopped, or crash-looping — unlike an exec into the running service.
        detect = next(
            (c for c in _command_strings()
             if "mysql.ibd" in c and "test -f" in c),
            None,
        )
        assert detect is not None, "detection must be a `test -f` on the volume"
        assert "docker run" in detect and "-v" in detect, (
            "detection must read the data volume from a throwaway container so "
            "it works even when the DB container is not running"
        )


class TestDumpFromDedicatedContainer:
    def test_dump_uses_temporary_mysql8_container(self):
        cmds = " \n".join(_command_strings())
        assert "mysql:8.0" in cmds, (
            "the dump must run against a temporary mysql:8.0 container the "
            "migration starts itself, not the instance's own db service"
        )

    def test_temp_container_is_always_removed(self):
        # It must be torn down on both the happy path and the failure path.
        removals = [c for c in _command_strings() if "docker rm -f" in c]
        assert len(removals) >= 2, (
            "the temporary container must be removed on success and cleaned up "
            "again in the rescue path"
        )

    def test_dump_excludes_system_schemas(self):
        dump = next(
            (c for c in _shell_text() if "mysqldump" in c), None
        )
        assert dump is not None, "a mysqldump command must exist"
        assert "--all-databases" not in dump, (
            "--all-databases drags MySQL 8's system schema into MariaDB and "
            "breaks the grant tables; dump only user databases"
        )
        for sysdb in ("mysql", "information_schema", "performance_schema", "sys"):
            assert sysdb in dump, (
                "the user-database query must exclude the %s system schema" % sysdb
            )

    def test_dump_carries_routines_triggers_events(self):
        dump = next(
            (c for c in _shell_text() if "mysqldump" in c), None
        )
        for flag in ("--routines", "--triggers", "--events"):
            assert flag in dump, (
                "mysqldump must pass %s to carry stored programs across" % flag
            )


class TestOrderingAndRecovery:
    def test_stop_before_dump(self):
        # A second mysqld must not open the datadir while the instance's own
        # db container still holds it.
        assert _index_of("Stop containers before dump") < _index_of(
            "temporary MySQL 8.0 container"
        ), "the instance must be stopped before the dump container starts"

    def test_compose_refreshed_between_clear_and_start(self):
        clear = _index_of("Clear the MySQL data volume")
        refresh = _index_of("Refresh compose to the managed MariaDB image")
        start = _index_of("Start MariaDB containers")
        assert clear < refresh < start, (
            "compose must be refreshed to the MariaDB image after the volume "
            "is cleared and before the DB is restarted, or MySQL 8 comes back"
        )

    def test_import_is_after_start(self):
        assert _index_of("Start MariaDB containers") < _index_of(
            "Import the dump into MariaDB"
        )

    def test_failure_preserves_dump_for_manual_recovery(self):
        fail = next(
            (t for t in _all_tasks() if "ansible.builtin.fail" in t), None
        )
        assert fail is not None, (
            "a rescue must fail loudly rather than swallowing a partial migration"
        )
        msg = str(fail["ansible.builtin.fail"].get("msg", ""))
        assert "preserved" in msg and "manual" in msg, (
            "the failure must tell the operator the dump is preserved for "
            "manual import"
        )


class TestInternalDbGate:
    def test_migration_gated_on_internal_db(self):
        # An external-DB instance may keep a stale internal data volume, but
        # its db service is not in the active compose profiles — migrating
        # would fail mid-way and take down a healthy site.
        top = _load()[0]
        cond = str(top.get("when", ""))
        assert "USE_EXTERNAL_DB" in cond, (
            "the migration must skip external-DB instances"
        )
        assert "COMPOSE_PROFILES" in cond and "internal-db" in cond, (
            "the migration must only run when the internal-db profile is "
            "active (or COMPOSE_PROFILES is not yet backfilled)"
        )


class TestVolumeExistenceCheck:
    def test_volume_inspected_before_detection_probe(self):
        # `docker run -v` auto-creates a missing named volume; probing an
        # instance that never had one would leave a stray empty volume.
        assert _index_of("Check whether the MySQL data volume exists") < (
            _index_of("Detect MySQL 8.0 data")
        ), "the volume-existence check must precede the detection probe"

    def test_existence_check_uses_volume_inspect(self):
        assert any(
            "docker volume inspect" in c for c in _command_strings()
        ), "existence must be checked with docker volume inspect, not a run"

    def test_detection_skipped_when_volume_missing(self):
        detect = next(
            t for t in _all_tasks()
            if t.get("name", "").startswith("Detect MySQL 8.0")
        )
        assert "_mig_volume_check.rc == 0" in str(detect.get("when", "")), (
            "the docker run probe must be skipped when the volume is missing"
        )

    def test_detection_flag_requires_existing_volume(self):
        flag = next(
            t for t in _all_tasks()
            if t.get("name") == "Set MySQL 8 detection flag"
        )
        expr = str(flag["ansible.builtin.set_fact"]["_is_mysql8"])
        assert "_mig_volume_check.rc == 0" in expr, (
            "_is_mysql8 must be false when the volume does not exist"
        )


class TestEmptyDatabaseList:
    def test_grep_no_match_neutralized_under_pipefail(self):
        # With only system schemas present, grep -v matches nothing and exits
        # 1; under pipefail that aborted the script before the empty-DBS
        # handler could write the empty dump.
        script = next(c for c in _shell_text() if "SHOW DATABASES" in c)
        assert "pipefail" in script, (
            "pipefail must stay on so mysql/mysqldump failures abort the dump"
        )
        assert "{ grep" in script and "|| true; }" in script, (
            "grep's no-match exit must be neutralized inside the pipeline so "
            "a system-schemas-only server reaches the empty-DBS branch"
        )

    def test_empty_list_writes_empty_dump(self):
        script = next(c for c in _shell_text() if "SHOW DATABASES" in c)
        assert 'if [ -z "$DBS" ]' in script, (
            "an empty database list must be handled explicitly"
        )


class TestDumpScriptQuoting:
    def test_dump_script_passed_through_quote_filter(self):
        dump_cmd = next(c for c in _command_strings() if "bash -c" in c)
        assert "_mig_dump_script | quote" in dump_cmd, (
            "the dump script must be escaped with the quote filter, not "
            "hand-wrapped in single quotes"
        )
        assert "'{{ _mig_dump_script }}'" not in dump_cmd


class TestNonRootUserWarning:
    def test_warns_that_non_root_users_are_not_migrated(self):
        # The dump excludes the mysql system schema, so a non-root
        # MYSQL_USER's account and grants do not survive the migration.
        warn = next(
            (t for t in _all_tasks()
             if t.get("name") == "Warn that non-root DB users are not migrated"),
            None,
        )
        assert warn is not None, (
            "the migration must warn when MYSQL_USER is a non-root user"
        )
        msg = str(warn["ansible.builtin.debug"].get("msg", ""))
        assert "grants" in msg and "MariaDB" in msg, (
            "the warning must tell the operator to recreate the user and its "
            "grants in MariaDB"
        )
        cond = str(warn.get("when", ""))
        assert "_mig_dbuser" in cond and "root" in cond, (
            "the warning must fire only for a non-root, non-empty MYSQL_USER"
        )


class TestFailurePathDumpIgnored:
    def test_gitignore_default_ignores_migration_dump(self):
        # A dump preserved by the rescue path sits in the instance root; the
        # gitops `git add -A` must never be able to commit it.
        with open(GITIGNORE) as f:
            content = f.read()
        assert "mysql8_dump.sql" in content, (
            "gitignore.default must ignore the preserved migration dump"
        )
