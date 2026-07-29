"""Creating the backup volume must tolerate it already existing.

`docker volume create` succeeds on an existing volume. `podman volume
create` does not:

    $ podman volume create zzz-test    # second time
    Error: volume with name zzz-test already exists: volume already exists
    rc=125

The task is named "Ensure backup volume exists" but ran the command
bare, so on Podman the first backup succeeded and every later one
failed.

The tolerance has to be narrow. Blanket-ignoring the failure would let
the backup proceed against a volume that was never created — a bad name
or a full disk would then surface much later, or not at all.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
RUN_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "run_backup.yml")


def _tasks():
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    with open(RUN_BACKUP) as f:
        walk(yaml.safe_load(f))
    return out


def _task():
    return next(
        (t for t in _tasks()
         if "Ensure backup volume exists" in str(t.get("name", ""))), None)


class TestAnExistingVolumeIsNotAFailure:
    def test_the_task_exists(self):
        assert _task()

    def test_it_registers_the_result(self):
        assert _task().get("register") == "_bvol_create", (
            "without registering, the failure cannot be inspected and the "
            "task fails the whole backup on podman"
        )

    def test_already_exists_is_tolerated(self):
        cond = _task().get("failed_when")
        assert cond, "a bare command fails on podman's exit 125"
        body = " ".join(cond) if isinstance(cond, list) else str(cond)
        assert "already exists" in body


class TestRealFailuresStillStop:
    def test_it_does_not_ignore_every_error(self):
        # `failed_when: false` or `ignore_errors: true` would let the
        # backup continue against a volume that does not exist.
        task = _task()
        assert task.get("ignore_errors") is not True
        assert task.get("failed_when") is not False

    def test_the_condition_keeps_the_nonzero_check(self):
        cond = _task().get("failed_when")
        body = " ".join(cond) if isinstance(cond, list) else str(cond)
        assert "rc != 0" in body, (
            "the tolerance must be scoped to the already-exists case, not "
            "applied to any exit status"
        )


class TestChangedReporting:
    def test_it_is_not_unconditionally_changed(self):
        # A volume that already existed was not created by this run.
        assert _task().get("changed_when") is not True
        assert "rc" in str(_task().get("changed_when"))
