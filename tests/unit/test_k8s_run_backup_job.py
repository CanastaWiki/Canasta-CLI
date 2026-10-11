"""The Kubernetes restic Job: which runs dump databases, what they dump,
how long the Job may run, and how a Job without a pod is reported."""

import os
import subprocess

import jinja2
import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JOB = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")
DEFAULTS = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "defaults", "main.yml")


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _task(name):
    with open(JOB) as f:
        tasks = list(_walk(yaml.safe_load(f)))
    task = next((t for t in tasks if t.get("name") == name), None)
    assert task is not None, "task %r missing/renamed" % name
    return task


def _template(expr, **variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _subcommand(args):
    expr = _task("Find the restic subcommand")[
        "ansible.builtin.set_fact"]["_restic_subcommand"]
    return _template(expr, backup_args=args)


def _is_backup_op(args):
    expr = _task("Detect backup operation")[
        "ansible.builtin.set_fact"]["_is_backup_op"]
    return _template(expr, _restic_subcommand=_subcommand(args))


class TestBackupOperationDetection:
    def test_create_is_a_backup(self):
        args = ["--host", "mysite", "--tag", "manual", "backup",
                "/currentsnapshot"]
        assert _subcommand(args) == "backup"
        assert _is_backup_op(args) is True

    def test_flag_values_named_like_subcommands_are_skipped(self):
        assert _subcommand(["--host", "check", "--tag", "forget",
                            "backup", "/currentsnapshot"]) == "backup"

    def test_purge_keeping_a_tag_named_backup_is_not_a_backup(self):
        args = ["forget", "--prune", "--keep-last", "3",
                "--keep-tag", "backup"]
        assert _subcommand(args) == "forget"
        assert _is_backup_op(args) is False

    @pytest.mark.parametrize("args, expected", [
        (["snapshots"], "snapshots"),
        (["snapshots", "--json", "--tag", "INCOMPLETE"], "snapshots"),
        (["restore", "abc123", "--target", "/"], "restore"),
        (["ls", "backup"], "ls"),
        (["--tag=x", "backup", "/currentsnapshot"], "backup"),
        (["init"], "init"),
    ])
    def test_other_operations(self, args, expected):
        assert _subcommand(args) == expected


@pytest.fixture
def fake_db(tmp_path):
    """mariadb lists databases; mariadb-dump records its arguments."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    (bindir / "mariadb").write_text(
        "#!/bin/sh\necho \"mariadb $*\" >> %s\n"
        "printf 'one\\none_cargo\\ntwo_cargo\\nloose\\nmysql\\n'\n" % calls)
    (bindir / "mariadb-dump").write_text(
        "#!/bin/sh\necho \"mariadb-dump $*\" >> %s\n"
        "echo \"-- dump of $*\"\n" % calls)
    for name in ("mariadb", "mariadb-dump"):
        (bindir / name).chmod(0o755)
    env = dict(os.environ, MYSQL_PASSWORD="pw", MYSQL_PORT="3307",
               PATH="%s:%s" % (bindir, os.environ["PATH"]))
    return env, calls


def _dump_container():
    task = _task("Build init containers for backup ops")
    return next(c for c in task["ansible.builtin.set_fact"][
        "_backup_init_containers"] if c["name"] == "dump-databases")


def _run_dump(tmp_path, env, groups, external):
    dumps = tmp_path / "dumps"
    status = tmp_path / "status"
    dumps.mkdir()
    status.mkdir()
    jenv = jinja2.Environment()
    jenv.filters["bool"] = bool
    script = jenv.from_string(_dump_container()["command"][-1]).render(
        _backup_groups_encoded=groups, _backup_external_db=external)
    script = script.replace(
        "/currentsnapshot/config/backup", str(dumps)).replace(
        "/dump-status", str(status))
    result = subprocess.run(["sh", "-c", script], capture_output=True,
                            text=True, env=env)
    assert result.returncode == 0, result.stderr
    failures = status / "failures"
    return (sorted(os.listdir(dumps)),
            failures.read_text().splitlines() if failures.exists() else [])


class TestDumpScript:
    def test_a_missing_wiki_database_is_recorded(self, tmp_path, fake_db):
        env, _ = fake_db
        files, failures = _run_dump(
            tmp_path, env, "one one_cargo;two two_cargo", False)
        assert failures == ["DUMP-FAILED two: database does not exist "]
        # The wiki's extra database that does exist is still captured.
        assert "db_two.sql" in files

    def test_a_missing_extra_database_is_recorded(self, tmp_path, fake_db):
        env, _ = fake_db
        _, failures = _run_dump(tmp_path, env, "one one_gone", False)
        assert failures == ["DUMP-FAILED one_gone: database does not exist "]

    def test_bundled_server_dumps_everything_else(self, tmp_path, fake_db):
        env, _ = fake_db
        files, _ = _run_dump(tmp_path, env, "one one_cargo", False)
        assert files == ["db_loose.sql", "db_one.sql", "db_two_cargo.sql"]

    def test_external_server_dumps_only_wiki_groups(self, tmp_path, fake_db):
        env, _ = fake_db
        files, failures = _run_dump(tmp_path, env, "one one_cargo", True)
        assert files == ["db_one.sql"]
        assert failures == []

    def test_every_client_call_passes_the_port(self, tmp_path, fake_db):
        env, calls = fake_db
        _run_dump(tmp_path, env, "one one_cargo", True)
        lines = calls.read_text().splitlines()
        assert lines
        for line in lines:
            assert " -P 3307 " in line, line

    @pytest.mark.parametrize("external, env_port, expected", [
        (True, "3307", "3307"),
        (True, "", "3306"),
        (False, "3307", "3306"),
    ])
    def test_the_port_comes_from_env_for_an_external_db(
            self, external, env_port, expected):
        env = _dump_container()["env"]
        port = next(e for e in env if e["name"] == "MYSQL_PORT")["value"]
        value = _template(port, _backup_external_db=external,
                          _backup_env={"variables": {"MYSQL_PORT": env_port}})
        assert value == expected
        assert isinstance(value, str)


class TestTimeout:
    def test_default_is_an_hour(self):
        with open(DEFAULTS) as f:
            assert yaml.safe_load(f)["canasta_k8s_backup_timeout"] == 3600

    @pytest.mark.parametrize("variables, retries", [
        ({}, 720),
        ({"canasta_k8s_backup_timeout": 7200}, 1440),
        ({"canasta_k8s_backup_timeout": "60"}, 12),
        ({"canasta_k8s_backup_timeout": 1}, 1),
    ])
    def test_the_wait_follows_the_timeout(self, variables, retries):
        wait = _task("Wait for restic Job to complete")
        assert wait["delay"] == 5
        assert int(_template(wait["retries"], **variables)) == retries

    def test_the_message_states_the_timeout_and_the_deletion(self):
        msg = _task("Fail when the Job did not finish")[
            "ansible.builtin.fail"]["msg"]
        assert "canasta_k8s_backup_timeout | default(3600)" in msg
        assert "10 minutes" not in msg
        assert "deleted" in msg and "stopped" in msg


class TestJobWithoutAPod:
    def test_finding_the_pod_does_not_fail_the_run(self):
        assert _task("Get restic Job pod name")["failed_when"] is False

    def test_pod_reads_are_skipped_without_a_pod(self):
        for name in ("Get restic output",
                     "Get the init containers' exit codes",
                     "Get why the Job's pod is stuck"):
            when = _task(name)["when"]
            when = when if isinstance(when, list) else [when]
            assert "_backup_pod != ''" in when, name

    def test_events_are_read_only_without_a_pod(self):
        task = _task("Get why the Job has no pod")
        assert task["when"] == "_backup_pod == ''"
        assert task["failed_when"] is False

    @pytest.mark.parametrize("stdout, pod", [
        ("", ""),
        ("restic-1-abc", "restic-1-abc"),
        ("restic-1-abc restic-1-def", "restic-1-abc"),
    ])
    def test_the_pod_name(self, stdout, pod):
        expr = _task("Record the restic Job's pod")[
            "ansible.builtin.set_fact"]["_backup_pod"]
        assert _template(expr, _backup_pod_name={"stdout": stdout}) == pod

    def _reason(self, **variables):
        task = _task("Fail when the Job did not finish")
        templar_vars = dict(variables)
        for key, expr in task["vars"].items():
            templar_vars[key] = _template(expr, **templar_vars)
        return templar_vars["_backup_stuck_reason"]

    def test_no_pod_is_reported_with_the_job_events(self):
        reason = self._reason(
            _backup_pod="",
            _backup_job_events={"stdout": (
                "FailedCreate: pods \"restic-1-x\" is forbidden: violates "
                "PodSecurity \"baseline:latest\": hostPath volumes\n")})
        assert reason.startswith(" No pod was created for it: FailedCreate")
        assert "PodSecurity" in reason

    def test_no_pod_and_no_events(self):
        assert self._reason(
            _backup_pod="", _backup_job_events={"stdout": ""}) == \
            " No pod was created for it."

    def test_a_waiting_pod_is_still_reported(self):
        reason = self._reason(
            _backup_pod="restic-1-abc",
            _backup_stuck_status={"stdout": (
                "dump-databases: ImagePullBackOff nope\nrestic:  \n")})
        assert reason == " Its pod was waiting: dump-databases: " \
            "ImagePullBackOff nope"

    def test_results_tolerate_a_skipped_log_read(self):
        stdout = _task("Set restic result")["ansible.builtin.set_fact"][
            "_restic_result"]["stdout"]
        assert "default('')" in stdout
        failures = _task("Collect the database dumps that failed")[
            "ansible.builtin.set_fact"]["_backup_dump_failures"]
        assert "_restic_log.stdout | default('')" in failures
