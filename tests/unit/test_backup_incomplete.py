"""A database that fails to dump must not cost the whole backup, and the
partial snapshot that results must never pass for a complete one.

Every database group is attempted; the snapshot is still taken, tagged
INCOMPLETE plus missing-db:<name> per absent database; create exits
non-zero (so a scheduled `create && purge` skips retention); `backup list`
calls the snapshot out; and `backup restore` refuses it unless
--allow-incomplete is given.
"""

import os
import shlex
import subprocess
import sys

import jinja2
import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import backup as backup_cmd  # noqa: E402

TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
BACKUP_TASKS = os.path.join(REPO_ROOT, "roles", "backup", "tasks")
STAGE = os.path.join(TASKS, "backup_stage_db_dumps.yml")
DUMP_GROUP = os.path.join(TASKS, "backup_dump_db_group.yml")
K8S_BACKUP = os.path.join(TASKS, "k8s_run_backup.yml")
RESTORE_INSTANCE = os.path.join(TASKS, "restore_instance.yml")
CREATE = os.path.join(BACKUP_TASKS, "create.yml")
RESTORE = os.path.join(BACKUP_TASKS, "restore.yml")
LIST = os.path.join(BACKUP_TASKS, "list.yml")
COMMAND_DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _walk(task[key])


def _tasks(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _by_name(path, name):
    task = next((t for t in _tasks(path) if t.get("name") == name), None)
    assert task is not None, "%s: task %r missing/renamed" % (path, name)
    return task


def _names(path):
    return [t.get("name") for t in _tasks(path)]


def _when(task):
    when = task.get("when", [])
    return " ".join(when if isinstance(when, list) else [str(when)])


def _render(template, **variables):
    env = jinja2.Environment(trim_blocks=True)
    env.filters["quote"] = lambda s: shlex.quote(str(s))
    return env.from_string(template).render(**variables)


@pytest.fixture
def fake_mariadb(tmp_path):
    """mariadb-dump that fails for any *_cargo database, as an orphaned
    view does, after writing partial output; mariadb lists databases."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    dump = bindir / "mariadb-dump"
    dump.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do case \"$a\" in *_cargo)\n"
        "  echo '-- partial'\n"
        "  echo \"mariadb-dump: Couldn't execute 'SHOW FIELDS FROM v': "
        "View references invalid table(s) (1356)\" >&2\n"
        "  exit 2;; esac; done\n"
        "echo \"-- dump of $*\"\n")
    dump.chmod(0o755)
    mariadb = bindir / "mariadb"
    mariadb.write_text(
        "#!/bin/sh\nprintf 'one\\none_cargo\\ntwo\\nloose\\nmysql\\n'\n")
    mariadb.chmod(0o755)
    env = dict(os.environ, MYSQL_PASSWORD="pw", MYSQL_ROOT_PASSWORD="rootpw",
               PATH="%s:%s" % (bindir, os.environ["PATH"]))
    return env


GROUPS = [
    {"wiki": "one", "databases": ["one", "one_cargo"]},
    {"wiki": "two", "databases": ["two"]},
]


class TestComposeDump:
    def _command(self):
        task = _by_name(STAGE, "Dump each wiki's database group (Compose)")
        return task["vars"]["dump_command"]

    def _run(self, tmp_path, env, group):
        """Run the group's dump script on the host, with the db container
        replaced by a local shell, as the bundled-database path does."""
        cmd = _render(self._command(), item=group, instance_path=str(tmp_path),
                      _db_admin_exec="", _db_admin_args="-u root",
                      _db_admin_pwd='MYSQL_PWD="$MYSQL_ROOT_PASSWORD"')
        return subprocess.run(["sh", "-c", cmd], capture_output=True,
                              text=True, env=env)

    def test_failures_start_empty_on_every_orchestrator(self):
        task = _by_name(STAGE, "Start with no failed database dumps")
        assert task["ansible.builtin.set_fact"]["_backup_dump_failures"] == []
        assert "when" not in task, (
            "create.yml reads _backup_dump_failures on both orchestrators")

    def test_each_group_records_its_failure(self):
        task = _by_name(STAGE, "Dump each wiki's database group (Compose)")
        assert task["ansible.builtin.include_tasks"] == \
            "backup_dump_db_group.yml"
        record = _by_name(DUMP_GROUP, "Record the group's dump failure")
        assert "_backup_dump_failures +" in \
            record["ansible.builtin.set_fact"]["_backup_dump_failures"]

    def test_a_failed_group_does_not_stop_the_others(
            self, tmp_path, fake_mariadb):
        staging = tmp_path / "config" / "backup"
        staging.mkdir(parents=True)
        # A previous run's dump must not stand in for a failed group.
        (staging / "db_one.sql").write_text("STALE")
        outputs = {}
        for group in GROUPS:
            result = self._run(tmp_path, fake_mariadb, group)
            assert result.returncode == 0, result.stderr
            outputs[group["wiki"]] = result.stdout
        assert sorted(os.listdir(staging)) == ["db_one.sql", "db_two.sql"]
        # The group failed together, so its databases were dumped one at a
        # time: the main database is kept, only the broken one is missing.
        one = (staging / "db_one.sql").read_text()
        assert "--databases one" in one
        assert "STALE" not in one and "partial" not in one
        failures = [line for line in outputs["one"].splitlines()
                    if line.startswith("DUMP-FAILED ")]
        assert len(failures) == 1
        assert failures[0].startswith("DUMP-FAILED one_cargo: ")
        assert "(1356)" in failures[0]
        assert outputs["two"] == ""

    def test_a_single_database_group_that_fails_is_reported(
            self, tmp_path, fake_mariadb):
        staging = tmp_path / "config" / "backup"
        staging.mkdir(parents=True)
        result = self._run(tmp_path, fake_mariadb,
                           {"wiki": "x_cargo", "databases": ["x_cargo"]})
        assert result.returncode == 0, result.stderr
        assert os.listdir(staging) == []
        assert result.stdout.startswith("DUMP-FAILED x_cargo: ")


class TestKubernetesDump:
    def _init_script(self):
        task = _by_name(K8S_BACKUP, "Build init containers for backup ops")
        for container in task["ansible.builtin.set_fact"][
                "_backup_init_containers"]:
            if container["name"] == "dump-databases":
                return container
        raise AssertionError("dump-databases init container not found")

    def test_failures_reach_restic_outside_the_snapshot(self):
        mounts = self._init_script()["volumeMounts"]
        assert {"name": "dump-status", "mountPath": "/dump-status"} in mounts
        volumes = _by_name(
            K8S_BACKUP, "Add dumps volume + mount-on-mount for backup ops")
        facts = volumes["ansible.builtin.set_fact"]
        assert "'dump-status', 'emptyDir'" in facts["_restic_volumes"]
        assert "'mountPath': '/dump-status'" in facts["_restic_volume_mounts"]

    def test_a_failed_group_does_not_stop_the_others(
            self, tmp_path, fake_mariadb):
        dumps = tmp_path / "dumps"
        status = tmp_path / "status"
        dumps.mkdir()
        status.mkdir()
        script = _render(self._init_script()["command"][-1],
                         _backup_groups_encoded="one one_cargo;two")
        script = script.replace(
            "/currentsnapshot/config/backup", str(dumps)).replace(
            "/dump-status", str(status))
        result = subprocess.run(["sh", "-c", script], capture_output=True,
                                text=True, env=fake_mariadb)
        assert result.returncode == 0, result.stderr
        assert sorted(os.listdir(dumps)) == [
            "db_loose.sql", "db_one.sql", "db_two.sql"]
        one = (dumps / "db_one.sql").read_text()
        assert "--databases one" in one and "partial" not in one
        failures = (status / "failures").read_text().splitlines()
        assert len(failures) == 1
        assert failures[0].startswith("DUMP-FAILED one_cargo: ")

    def _tag_command(self, tmp_path, failures):
        status = tmp_path / "status"
        status.mkdir(exist_ok=True)
        if failures:
            (status / "failures").write_text(failures)
        task = _by_name(K8S_BACKUP,
                        "Tag the snapshot INCOMPLETE when a dump failed")
        assert task["when"] == "_is_backup_op | bool"
        cmd = _render(task["ansible.builtin.set_fact"]["_restic_command"],
                      _restic_command="echo restic backup /currentsnapshot")
        result = subprocess.run(
            ["sh", "-c", cmd.replace("/dump-status", str(status))],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.splitlines()

    def test_snapshot_is_tagged_incomplete(self, tmp_path):
        out = self._tag_command(
            tmp_path, "DUMP-FAILED one one_cargo: boom: (1356)\n"
                      "DUMP-FAILED loose: dump is empty\n")
        assert out[0] == "DUMP-FAILED one one_cargo: boom: (1356)"
        assert out[-1] == (
            "restic backup /currentsnapshot --tag INCOMPLETE,"
            "missing-db:one,missing-db:one_cargo,missing-db:loose")

    def test_complete_snapshot_is_untagged(self, tmp_path):
        assert self._tag_command(tmp_path, "") == [
            "restic backup /currentsnapshot"]

    def test_tagging_precedes_the_ownership_wrapper(self):
        # The wrapper captures restic's exit code with $?, so restic must
        # stay the last command before it.
        names = _names(K8S_BACKUP)
        assert names.index("Tag the snapshot INCOMPLETE when a dump failed") \
            < names.index("Reclaim local-path repo ownership after the op")

    def test_failures_are_read_back_from_the_job_log(self):
        task = _by_name(K8S_BACKUP, "Collect the database dumps that failed")
        expr = task["ansible.builtin.set_fact"]["_backup_dump_failures"]
        assert "_restic_log.stdout" in expr
        assert "^DUMP-FAILED " in expr


class TestCreate:
    def test_snapshot_tagged_from_failures(self):
        task = _by_name(CREATE, "Tag a snapshot that is missing databases")
        expr = task["ansible.builtin.set_fact"]["_backup_tag"]
        assert "'INCOMPLETE'" in expr and "missing-db:" in expr
        assert task["when"] == "_backup_dump_failures | length > 0"
        names = _names(CREATE)
        assert names.index("Tag a snapshot that is missing databases") < \
            names.index("Create backup snapshot")

    def test_create_fails_after_snapshot_and_cleanup(self):
        names = _names(CREATE)
        fail = names.index("Fail when databases are missing from the snapshot")
        assert fail > names.index("Create backup snapshot")
        assert fail > names.index("Clean up backup staging directory")
        task = _by_name(CREATE,
                        "Fail when databases are missing from the snapshot")
        assert "ansible.builtin.fail" in task
        assert "_backup_dump_failures" in task["ansible.builtin.fail"]["msg"]


class TestRestore:
    def test_listing_runs_for_explicit_snapshots_too(self):
        task = _by_name(RESTORE, "List all snapshots as JSON")
        assert "when" not in task

    def test_incomplete_snapshot_refused_without_override(self):
        task = _by_name(RESTORE, "Refuse an INCOMPLETE snapshot")
        when = _when(task)
        assert "_restore_incomplete" in when
        assert "not (allow_incomplete" in when
        assert "--allow-incomplete" in task["ansible.builtin.fail"]["msg"]
        assert "_restore_missing_dbs" in task["ansible.builtin.fail"]["msg"]

    def test_refusal_precedes_any_change(self):
        names = _names(RESTORE)
        refuse = names.index("Refuse an INCOMPLETE snapshot")
        assert refuse < names.index("Mark the restore as in flight")
        assert refuse < names.index("Restore from snapshot")

    def test_latest_skips_incomplete_unless_allowed(self):
        task = _by_name(RESTORE, "Pick latest non-safety snapshot")
        when = _when(task)
        assert "'INCOMPLETE' not in" in when
        assert "allow_incomplete" in when
        assert "'safety-before-restore' not in" in when

    def test_compose_import_skips_absent_dumps_only_when_incomplete(self):
        task = _by_name(RESTORE_INSTANCE, "Import each wiki database dump")
        assert "_restore_import_wikis" in task["loop"]
        choose = _by_name(RESTORE_INSTANCE,
                          "Choose the wikis whose databases to import")
        expr = choose["ansible.builtin.set_fact"]["_restore_import_wikis"]
        assert "if not (_restore_incomplete" in expr

    def test_flag_is_defined(self):
        with open(COMMAND_DEFS) as f:
            defs = yaml.safe_load(f)
        restore = next(c for c in defs["commands"]
                       if c["name"] == "backup_restore")
        flag = next(p for p in restore["parameters"]
                    if p["name"] == "allow_incomplete")
        assert flag["type"] == "bool"
        assert flag["default"] is False


class TestListAnsible:
    def test_incomplete_snapshots_are_called_out(self):
        query = _by_name(LIST, "List INCOMPLETE snapshots as JSON")
        assert query["vars"]["backup_args"] == [
            "snapshots", "--json", "--tag", "INCOMPLETE"]
        warn = _by_name(LIST, "Warn about INCOMPLETE snapshots")
        assert "WARNING" in warn["ansible.builtin.debug"]["msg"]
        assert "missing-db:" in warn["ansible.builtin.debug"]["msg"]


class TestListFastPath:
    SNAPSHOTS_JSON = (
        '[{"id": "bbbb2222ffff", "short_id": "bbbb2222",'
        ' "time": "2026-09-02T02:00:00.123Z",'
        ' "tags": ["nightly__on__h", "INCOMPLETE",'
        ' "missing-db:one", "missing-db:one_cargo"]}]')

    def test_warning_names_missing_databases(self):
        warning = backup_cmd.incomplete_warning(self.SNAPSHOTS_JSON)
        assert warning.startswith("WARNING: 1 INCOMPLETE snapshot(s)")
        assert "bbbb2222  2026-09-02 02:00:00  missing: one, one_cargo" \
            in warning

    def test_no_warning_without_incomplete_snapshots(self):
        assert backup_cmd.incomplete_warning("[]") == ""
        assert backup_cmd.incomplete_warning("not json") == ""

    def _run_list(self, monkeypatch, table):
        monkeypatch.setattr(
            direct_commands._helpers, "_resolve_instance",
            lambda args: ("test", {"path": "/srv/test",
                                   "orchestrator": "compose"}))
        monkeypatch.setattr(
            direct_commands._helpers, "_read_env_file",
            lambda *a: {"RESTIC_REPOSITORY": "s3:s3.amazonaws.com/b"})
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv[-1])
            out = self.SNAPSHOTS_JSON if "--json" in argv[-1] else table
            return type("R", (), {"returncode": 0, "stdout": out,
                                  "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc = direct_commands.cmd_backup_list(
            type("Args", (), {"id": "test"})())
        return rc, calls

    def test_list_warns_below_the_table(self, monkeypatch, capsys):
        rc, calls = self._run_list(
            monkeypatch,
            "ID        Tags\nbbbb2222  nightly__on__h\n          INCOMPLETE\n")
        assert rc == 0
        assert len(calls) == 2
        assert "snapshots --json --tag INCOMPLETE" in calls[1]
        out = capsys.readouterr().out
        assert out.index("bbbb2222  nightly") < out.index("WARNING: 1 INCOMPLETE")
        assert "missing: one, one_cargo" in out

    def test_complete_listing_runs_restic_once(self, monkeypatch, capsys):
        rc, calls = self._run_list(
            monkeypatch, "ID        Tags\naaaa1111  nightly__on__h\n")
        assert rc == 0
        assert len(calls) == 1
        assert "WARNING" not in capsys.readouterr().out
