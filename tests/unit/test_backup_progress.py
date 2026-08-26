"""`backup create` and `backup restore` must show that they are working.

Both printed nothing at all between the command starting and its final
result. Three layers suppressed output independently: the minimal stdout
callback drops everything but debug messages, `shell` + `register:`
buffers a task's output until it finishes, and restic prints its progress
line only to a terminal. On an instance with a large images/ tree that is
a long silence with no way to tell a working run from a stalled one.

RESTIC_PROGRESS_FPS makes restic print progress with no terminal
attached; the log is where an operator can watch it while the task is
still running.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RUN_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "run_backup.yml")
K8S_RUN_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")
CREATE = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "create.yml")
RESTORE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _tasks(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _task(path, name):
    return next(t for t in _tasks(path) if t.get("name") == name)


def _debug_messages(path):
    out = []
    for t in _tasks(path):
        d = t.get("ansible.builtin.debug") or t.get("debug")
        if isinstance(d, dict) and d.get("msg"):
            out.append(str(d["msg"]))
    return out


class TestResticProgress:
    def test_compose_run_sets_progress_fps(self):
        cmd = _task(RUN_BACKUP, "Run restic container")["ansible.builtin.shell"]["cmd"]
        assert "RESTIC_PROGRESS_FPS" in cmd, (
            "restic prints progress only to a terminal, and the container "
            "has none"
        )

    def test_kubernetes_job_sets_progress_fps(self):
        with open(K8S_RUN_BACKUP) as f:
            content = f.read()
        assert "RESTIC_PROGRESS_FPS" in content, (
            "a Job's container has no terminal either, so `kubectl logs -f` "
            "shows nothing while a long backup runs"
        )


class TestResticOutputIsLogged:
    def _run_task(self):
        return _task(RUN_BACKUP, "Run restic container")

    def test_output_is_teed_to_the_instance_log(self):
        cmd = self._run_task()["ansible.builtin.shell"]["cmd"]
        assert "_backup_tee" in cmd, (
            "the task buffers its output until it finishes, so the log is "
            "the only way to watch a run in progress"
        )

    def test_the_tee_does_not_mask_a_restic_failure(self):
        task = self._run_task()
        cmd = task["ansible.builtin.shell"]["cmd"]
        assert "set -o pipefail" in cmd, (
            "piping into tee makes the task's rc come from tee, so a failed "
            "restic would read as a successful backup"
        )
        executable = str(
            (task.get("args") or {}).get("executable")
            or task["ansible.builtin.shell"].get("executable", "")
        )
        assert "bash" in executable, "pipefail needs bash, not /bin/sh"

    def test_only_the_long_operations_are_logged(self):
        """A snapshot listing in backup.log buries what it is read for."""
        decide = _task(RUN_BACKUP, "Decide where this restic run is logged")
        tee = decide["ansible.builtin.set_fact"]["_backup_tee"]
        assert "backup_args | intersect(['backup', 'restore'])" in tee
        assert "backup.log" in tee


class TestPhasesAreAnnounced:
    """debug is the one thing the minimal stdout callback lets through, so
    it is what tells an operator which phase is running without --verbose."""

    def test_backup_announces_its_phases(self):
        messages = " ".join(_debug_messages(CREATE) + _debug_messages(RUN_BACKUP))
        assert "Dumping databases" in messages
        assert "Staging" in messages

    def test_restore_announces_its_phases(self):
        messages = " ".join(_debug_messages(RESTORE))
        assert "Clearing the staging volume" in messages
        assert "Copying the restored files" in messages
        assert "Importing the restored databases" in messages
