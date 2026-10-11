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

    def test_the_long_wait_runs_only_for_a_job_with_a_pod(self):
        wait = _task("Wait for restic Job to complete")
        assert wait["when"] == "_backup_pod_created | bool"

    def test_the_message_states_the_timeout_and_the_deletion(self):
        summary = _unfinished_vars(
            _backup_pod_created=True, _backup_pod="restic-1-abc",
            _restic_subcommand="backup",
            canasta_k8s_backup_timeout=7200)["_backup_unfinished_summary"]
        assert summary == (
            "did not finish within 7200 seconds. The Job and its pod were "
            "deleted, so restic backup was stopped and did not complete.")


def _unfinished_vars(**variables):
    task = _task("Fail when the Job did not finish")
    rendered = dict(variables)
    for key, expr in task["vars"].items():
        rendered[key] = _template(expr, **rendered)
    return rendered


@pytest.fixture
def fake_kubectl(tmp_path):
    """kubectl whose pod list and FailedCreate events come from files."""
    bindir = tmp_path / "kbin"
    bindir.mkdir()
    (bindir / "kubectl").write_text(
        "#!/bin/sh\n"
        "echo \"$*\" >> %(d)s/calls\n"
        "case \"$2\" in\n"
        "  pods) cat %(d)s/pods 2>/dev/null ;;\n"
        "  events) cat %(d)s/events 2>/dev/null ;;\n"
        "esac\n"
        "exit 0\n" % {"d": tmp_path})
    (bindir / "kubectl").chmod(0o755)
    return tmp_path, dict(os.environ,
                          PATH="%s:%s" % (bindir, os.environ["PATH"]))


class TestWaitForThePod:
    def _task(self):
        return _task("Wait for the restic Job's pod to be created")

    def _poll(self, fake_kubectl, pods="", events=""):
        tmp_path, env = fake_kubectl
        if pods:
            (tmp_path / "pods").write_text(pods)
        if events:
            (tmp_path / "events").write_text(events)
        argv = [_template(a, _k8s_namespace="canasta-x",
                          _backup_job_name="restic-1")
                for a in self._task()["ansible.builtin.command"]["argv"]]
        result = subprocess.run(argv, capture_output=True, text=True,
                                env=env)
        register = {"rc": result.returncode, "stdout": result.stdout,
                    "stdout_lines": result.stdout.splitlines()}
        done = _template("{{ %s }}" % self._task()["until"],
                         _backup_pod_wait=register)
        created = _template(
            _task("Record whether the Job got a pod")[
                "ansible.builtin.set_fact"]["_backup_pod_created"],
            _backup_pod_wait=register)
        calls = (tmp_path / "calls").read_text()
        return done, created, calls

    def test_a_pod_ends_the_wait(self, fake_kubectl):
        done, created, calls = self._poll(fake_kubectl, pods="restic-1-abc")
        assert (done, created) == (True, True)
        assert "-n canasta-x" in calls and "job-name=restic-1" in calls

    def test_failed_create_ends_the_wait_without_a_pod(self, fake_kubectl):
        done, created, calls = self._poll(
            fake_kubectl, events="event/restic-1.17f\n")
        assert (done, created) == (True, False)
        assert "reason=FailedCreate" in calls
        assert "involvedObject.name=restic-1" in calls

    def test_nothing_yet_keeps_waiting(self, fake_kubectl):
        done, created, _ = self._poll(fake_kubectl)
        assert (done, created) == (False, False)

    def test_the_wait_is_short_and_never_fails_the_run(self):
        task = self._task()
        assert task["failed_when"] is False
        assert task["delay"] == 2
        seconds = task["vars"]["_backup_pod_wait_seconds"]
        assert seconds == 120
        assert int(_template(task["retries"],
                             _backup_pod_wait_seconds=seconds)) == 60

    def test_a_failed_kubectl_is_not_a_created_pod(self):
        expr = _task("Record whether the Job got a pod")[
            "ansible.builtin.set_fact"]["_backup_pod_created"]
        assert _template(expr, _backup_pod_wait={
            "rc": 1, "stdout_lines": ["x"]}) is False

    def test_it_runs_before_the_long_wait(self):
        with open(JOB) as f:
            names = [t.get("name") for t in yaml.safe_load(f)]
        assert names.index("Wait for the restic Job's pod to be created") \
            < names.index("Record whether the Job got a pod") \
            < names.index("Wait for restic Job to complete")


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
        assert task["when"] == (
            "_backup_pod == '' or not (_backup_pod_created | bool)")
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

    def test_no_pod_is_reported_with_the_job_events(self):
        rendered = _unfinished_vars(
            _backup_pod="", _backup_pod_created=False,
            _restic_subcommand="backup",
            _backup_job_events={"stdout": (
                "FailedCreate: pods \"restic-1-x\" is forbidden: violates "
                "PodSecurity \"baseline:latest\": hostPath volumes\n")})
        assert rendered["_backup_unfinished_summary"] == (
            "got no pod, so restic backup never started. "
            "The Job was deleted.")
        assert rendered["_backup_stuck_reason"].startswith(
            " Kubernetes reported: FailedCreate")
        assert "PodSecurity" in rendered["_backup_stuck_reason"]

    def test_no_pod_and_no_events(self):
        rendered = _unfinished_vars(
            _backup_pod="", _backup_pod_created=False,
            _restic_subcommand="snapshots",
            _backup_job_events={"stdout": ""})
        assert rendered["_backup_stuck_reason"] == ""
        assert "never started" in rendered["_backup_unfinished_summary"]

    def test_a_waiting_pod_is_still_reported(self):
        rendered = _unfinished_vars(
            _backup_pod="restic-1-abc", _backup_pod_created=True,
            _restic_subcommand="backup",
            _backup_stuck_status={"stdout": (
                "dump-databases: ImagePullBackOff nope\nrestic:  \n")})
        assert rendered["_backup_stuck_reason"] == (
            " Its pod was waiting: dump-databases: ImagePullBackOff nope")

    def test_success_is_read_from_the_recorded_fact(self):
        assert "_backup_job_succeeded | bool" in _task("Get restic output")[
            "failed_when"]
        expr = _task("Record whether the Job finished")[
            "ansible.builtin.set_fact"]["_backup_job_succeeded"]
        assert _template(expr, _backup_job_status={"skipped": True}) is False
        assert _template(expr, _backup_job_status={
            "resources": [{"status": {"succeeded": 1}}]}) is True

    def test_results_tolerate_a_skipped_log_read(self):
        stdout = _task("Set restic result")["ansible.builtin.set_fact"][
            "_restic_result"]["stdout"]
        assert "default('')" in stdout
        failures = _task("Collect the database dumps that failed")[
            "ansible.builtin.set_fact"]["_backup_dump_failures"]
        assert "_restic_log.stdout | default('')" in failures
