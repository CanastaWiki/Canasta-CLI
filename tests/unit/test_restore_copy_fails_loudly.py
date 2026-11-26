"""A restore empties each target directory before copying the snapshot in.

A copy that fails after that point (a full disk, for example) must fail the
task, or the restore carries on and reports success over emptied or
half-filled directories.
"""

import os

import yaml

from _k8s_restore_copy import resolve_copy_task

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")

COMPOSE_COPY_TASKS = (
    "Copy files from volume to host",
    "Copy single wiki's files from volume to host",
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
    return resolve_copy_task(next(t for t in tasks if t.get("name") == name))


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
