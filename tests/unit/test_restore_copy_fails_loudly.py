"""A restore empties each target directory before copying the snapshot in.

A copy that fails after that point (a full disk, for example) must fail the
task, or the restore carries on and reports success over emptied or
half-filled directories.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")
K8S = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml")

COMPOSE_COPY_TASKS = (
    "Copy files from volume to host",
    "Copy single wiki's files from volume to host",
)
K8S_COPY_TASKS = (
    "Copy restored config to host",
    "Copy restored single wiki settings to host",
    "Restore extensions/skins/public_assets to host",
    "Restore single wiki public assets to host",
)


def _flatten(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            if key in t:
                yield from _flatten(t[key])


def _task(path, name):
    with open(path) as f:
        tasks = list(_flatten(yaml.safe_load(f)))
    return next(t for t in tasks if t.get("name") == name)


def _container_script(cmd):
    return cmd[cmd.index("sh -c '") + len("sh -c '"):]


def _assert_errors_not_swallowed(task, name):
    assert not task.get("ignore_errors"), (
        "%s must not ignore errors" % name)
    assert "failed_when" not in task, (
        "%s must fail on a non-zero exit" % name)


def test_compose_copy_scripts_stop_on_the_first_failure():
    for name in COMPOSE_COPY_TASKS:
        task = _task(COMPOSE, name)
        script = _container_script(task["ansible.builtin.shell"]["cmd"])
        first = script.strip().splitlines()[0].strip()
        assert first == "set -e;", (
            "%s: the container script must begin with set -e so a failed cp "
            "fails the task" % name)
        _assert_errors_not_swallowed(task, name)


def test_compose_copy_scripts_keep_the_glob_clear_best_effort():
    # Under set -e an unguarded glob rm would abort a legitimate restore on
    # any leftover it cannot remove, before the copy has run.
    for name in COMPOSE_COPY_TASKS:
        script = _container_script(
            _task(COMPOSE, name)["ansible.builtin.shell"]["cmd"])
        for line in script.splitlines():
            if "rm -rf" in line and "/.[!.]*" in line:
                assert line.rstrip().endswith("|| true;"), (
                    "%s: glob rm must stay best-effort: %r" % (name, line))


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
