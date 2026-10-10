"""A failed restic Job must fail the command on Kubernetes, as on Compose.

k8s_run_backup.yml waits for the Job to succeed or fail and turns the
outcome into _restic_result.rc. Every backup command only prints
_restic_result.stdout, so unless the rc is checked here a wrong password,
a locked repository or a full disk exits 0, and a scheduled
`create && purge` goes on to purge.
"""

import os
import re

import jinja2
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JOB = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")
BACKUP_TASKS = os.path.join(REPO_ROOT, "roles", "backup", "tasks")


def _tasks():
    with open(JOB) as f:
        return yaml.safe_load(f)


def _index(name):
    names = [t.get("name") for t in _tasks()]
    return names.index(name)


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _fail_task():
    return _tasks()[_index("Fail when restic fails")]


READ_AND_DELETE = "Read the restic Job's output and delete the Job"


def _read_and_delete():
    return _tasks()[_index(READ_AND_DELETE)]


def _block_task(name):
    return next(t for t in _read_and_delete()["block"] if t["name"] == name)


class TestTheJobIsAlwaysDeleted:
    """Reading the Job's output can fail (no pod, no logs); the Job must
    be deleted regardless."""

    def test_reading_the_output_is_in_a_block(self):
        names = [t["name"] for t in _read_and_delete()["block"]]
        assert "Get restic Job pod name" in names
        assert "Get restic output" in names

    def test_the_job_is_deleted_in_always(self):
        always = _read_and_delete()["always"]
        assert [t["name"] for t in always] == ["Delete completed Job"]
        assert always[0]["kubernetes.core.k8s"]["state"] == "absent"

    def test_the_block_is_unconditional_and_includes_nothing(self):
        # Its registered results are consumed after it.
        task = _read_and_delete()
        assert "when" not in task
        for sub in _walk(task["block"] + task["always"]):
            assert "ansible.builtin.include_tasks" not in sub


class TestAFailedInitContainerIsReported:
    """A failed init container (e.g. the database dump) means restic never
    started and has no logs; its own logs carry the error."""

    def test_missing_restic_logs_fail_only_a_successful_job(self):
        when = _block_task("Get restic output")["failed_when"]
        assert "_restic_log.rc != 0" in when
        assert any("succeeded" in c for c in when)

    def test_init_statuses_are_read_only_without_restic_logs(self):
        task = _block_task("Get the init containers' exit codes")
        assert task["when"] == "_restic_log.rc != 0"
        assert task["failed_when"] is False
        assert "initContainerStatuses" in task["ansible.builtin.command"]["cmd"]

    def test_the_failed_init_containers_logs_are_read(self):
        task = _block_task("Get the failed init container's output")
        assert "-c {{ _backup_failed_init }}" in (
            task["ansible.builtin.command"]["cmd"])
        assert task["when"] == "_backup_failed_init != ''"
        assert task["failed_when"] is False

    def test_the_init_failure_is_picked_from_the_exit_codes(self):
        expr = _block_task("Find the init container that failed")[
            "ansible.builtin.set_fact"]["_backup_failed_init"]
        env = jinja2.Environment()
        env.tests["match"] = lambda s, p: re.match(p, s)
        env.filters["split"] = lambda s, sep: s.split(sep)

        def pick(stdout):
            return env.from_string(expr).render(
                _backup_init_status={"stdout": stdout}).strip()

        assert pick("dump-databases=1 dump-secrets= ") == "dump-databases"
        assert pick("dump-databases=0 dump-secrets=2 ") == "dump-secrets"
        assert pick("dump-databases=0 dump-secrets=0 ") == ""
        assert pick("") == ""

    def test_the_init_failure_fails_with_its_output_after_deletion(self):
        name = "Fail when an init container fails"
        task = _tasks()[_index(name)]
        assert _index(READ_AND_DELETE) < _index(name)
        assert _index("Set restic result") < _index(name)
        assert _index(name) < _index("Fail when restic fails")
        assert "_backup_init_output" in task["ansible.builtin.fail"]["msg"]
        assert "_backup_init_log.stdout" in str(task["vars"])
        assert "_restic_result.rc | int != 0" in task["when"]
        assert "_backup_failed_init != ''" in task["when"]


class TestTheJobOutcomeIsChecked:
    def test_a_non_zero_rc_fails(self):
        task = _fail_task()
        assert "ansible.builtin.fail" in task
        assert "_restic_result.rc" in str(task["when"])
        assert "!= 0" in str(task["when"])

    def test_the_rc_is_set_from_the_job_status_first(self):
        assert _index("Set restic result") < _index("Fail when restic fails")

    def test_the_failed_job_is_deleted_before_failing(self):
        assert _index(READ_AND_DELETE) < _index("Fail when restic fails")

    def test_the_message_carries_restic_output(self):
        task = _fail_task()
        assert "_restic_failed_output" in task["ansible.builtin.fail"]["msg"]
        assert "_restic_log.stdout" in str(task["vars"])

    def test_the_message_names_the_restic_command(self):
        task = _fail_task()
        assert "_restic_failed_command" in task["ansible.builtin.fail"]["msg"]
        assert "backup_args" in str(task["vars"]["_restic_failed_command"])

    def test_the_restic_command_line_is_not_clobbered(self):
        # _restic_command is the Job's shell command; the failure message
        # must not reuse that name.
        assert "_restic_command" not in _fail_task()["vars"]


class TestNoCallerSwallowsTheFailure:
    def test_backup_tasks_do_not_ignore_run_backup_errors(self):
        # Compose already fails on any non-zero restic exit, so no caller
        # relies on a failed run returning normally.
        for name in sorted(os.listdir(BACKUP_TASKS)):
            if not name.endswith(".yml"):
                continue
            with open(os.path.join(BACKUP_TASKS, name)) as f:
                tasks = yaml.safe_load(f) or []
            for task in _walk(tasks):
                if "run_backup.yml" in str(
                        task.get("ansible.builtin.include_tasks", "")):
                    assert "ignore_errors" not in task, name
                    assert "failed_when" not in task, name
