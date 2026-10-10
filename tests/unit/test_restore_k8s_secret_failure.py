"""A failed Secret restore on Kubernetes must say that it failed.

The task that applies the snapshot's Secret manifest is no_log, which
censors a failure along with the Secret, leaving a restore that exits 2
with nothing on screen. A separate task raises the failure, naming the
step and the exit code without repeating output that can contain Secret
values.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RESTORE = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml")
APPLY = "Restore canasta-managed K8s Secrets from snapshot"
FAIL = "Fail when the Secrets could not be restored"


def _tasks():
    with open(RESTORE) as f:
        return yaml.safe_load(f)


def _named(name):
    return next(t for t in _tasks() if t.get("name") == name)


def _index(name):
    return [t.get("name") for t in _tasks()].index(name)


class TestTheApplyStaysHidden:
    def test_it_is_still_no_log(self):
        assert _named(APPLY)["no_log"] is True

    def test_it_registers_instead_of_failing(self):
        task = _named(APPLY)
        assert task["failed_when"] is False
        assert task["register"] == "_restore_secrets_apply"


class TestTheFailureIsReported:
    def test_it_follows_the_apply(self):
        assert _index(FAIL) == _index(APPLY) + 1

    def test_it_fails_on_a_non_zero_rc(self):
        task = _named(FAIL)
        assert "ansible.builtin.fail" in task
        when = " ".join(task["when"])
        assert "_restore_secrets_apply.rc" in when
        assert "!= 0" in when
        assert "_restore_secrets_filename != ''" in when

    def test_it_is_visible(self):
        assert not _named(FAIL).get("no_log")

    def test_it_reports_the_rc_and_how_to_diagnose(self):
        msg = _named(FAIL)["ansible.builtin.fail"]["msg"]
        assert "_restore_secrets_apply.rc" in msg
        assert "kubectl apply" in msg and "--dry-run=server" in msg

    def test_it_never_repeats_the_output(self):
        msg = _named(FAIL)["ansible.builtin.fail"]["msg"]
        assert "stderr" not in msg
        assert "stdout" not in msg

    def test_it_comes_before_the_restore_pod_is_deleted(self):
        # The diagnosis command reads the manifest from that pod.
        assert _index(FAIL) < _index("Delete restore pod")
