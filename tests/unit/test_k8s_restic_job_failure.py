"""A failed restic Job must fail the command on Kubernetes, as on Compose.

k8s_run_backup.yml waits for the Job to succeed or fail and turns the
outcome into _restic_result.rc. Every backup command only prints
_restic_result.stdout, so unless the rc is checked here a wrong password,
a locked repository or a full disk exits 0, and a scheduled
`create && purge` goes on to purge.
"""

import os

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


class TestTheJobOutcomeIsChecked:
    def test_a_non_zero_rc_fails(self):
        task = _fail_task()
        assert "ansible.builtin.fail" in task
        assert "_restic_result.rc" in str(task["when"])
        assert "!= 0" in str(task["when"])

    def test_the_rc_is_set_from_the_job_status_first(self):
        assert _index("Set restic result") < _index("Fail when restic fails")

    def test_the_failed_job_is_deleted_before_failing(self):
        assert _index("Delete completed Job") < _index("Fail when restic fails")

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
