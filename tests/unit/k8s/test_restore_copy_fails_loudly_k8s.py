"""A Kubernetes restore copies the snapshot out of the restore pod through a
tar pipeline under bash's pipefail, so a failure on either side fails the
task; the config copies stop at the first failing command, and a snapshot
without config/ leaves the host config untouched."""

import os

from test_restore_copy_fails_loudly import (
    REPO_ROOT,
    _assert_errors_not_swallowed,
    _task,
)


K8S = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_restore_instance.yml")


K8S_COPY_TASKS = (
    "Copy restored config to host",
    "Copy restored single wiki settings to host",
    "Restore extensions/skins/public_assets to host",
    "Restore single wiki public assets to host",
    "Restore sidecar build contexts to host",
)


def test_k8s_copy_scripts_fail_on_any_pipeline_stage():
    for name in K8S_COPY_TASKS:
        task = _task(K8S, name)
        cmd = task["ansible.builtin.shell"]["cmd"]
        assert "pipefail" in cmd, (
            "%s: the tar pipeline must fail when either side fails" % name)
        assert task.get("args", {}).get("executable") == "/bin/bash", (
            "%s: pipefail needs bash, not /bin/sh" % name)
        _assert_errors_not_swallowed(task, name)


def test_k8s_config_copies_stop_on_the_first_failure():
    for name in ("Copy restored config to host",
                 "Copy restored single wiki settings to host"):
        cmd = _task(K8S, name)["ansible.builtin.shell"]["cmd"]
        assert cmd.startswith("set -eo pipefail;"), (
            "%s must stop at the first failing command" % name)


def test_k8s_full_config_restore_skips_a_snapshot_without_config():
    cmd = _task(K8S, "Copy restored config to host")[
        "ansible.builtin.shell"]["cmd"]
    guard = "test -d /currentsnapshot/config; then"
    assert guard in cmd, (
        "a snapshot without config/ must leave the host config untouched "
        "rather than abort the restore")
    assert cmd.index("if kubectl exec") < cmd.index(guard) < cmd.index(
        "rm -rf"), "the host config must be cleared only inside the guard"
    assert cmd.rstrip().endswith("fi")
