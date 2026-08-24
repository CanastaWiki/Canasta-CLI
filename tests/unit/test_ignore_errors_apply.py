"""Guard against inert `ignore_errors` on `include_tasks`.

`ignore_errors: true` placed directly on a dynamic `include_tasks` is inert —
it applies to the include operation, not to the tasks inside the included file,
so a non-zero result there aborts the whole play. This silently broke
`canasta remove` (its first exec aborted the play before the wiki was removed).
The correct form puts the flag under the include's `apply:`. This test fails if
any task file reintroduces the top-level pattern.
"""

import glob
import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

INCLUDE_KEYS = ("ansible.builtin.include_tasks", "include_tasks")


def _task_files():
    files = glob.glob(
        os.path.join(REPO_ROOT, "roles", "**", "tasks", "**", "*.yml"),
        recursive=True)
    files += glob.glob(os.path.join(REPO_ROOT, "playbooks", "*.yml"))
    return files


def _flatten(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            if key in t:
                yield from _flatten(t[key])


def test_no_inert_ignore_errors_on_include_tasks():
    offenders = []
    for path in _task_files():
        with open(path) as f:
            try:
                doc = yaml.safe_load(f)
            except yaml.YAMLError:
                continue
        if not isinstance(doc, list):
            continue
        for task in _flatten(doc):
            has_include = any(k in task for k in INCLUDE_KEYS)
            if has_include and "ignore_errors" in task:
                rel = os.path.relpath(path, REPO_ROOT)
                offenders.append("%s: %s" % (rel, task.get("name", "?")))
    assert not offenders, (
        "include_tasks with a task-level ignore_errors (inert — move it under "
        "the include's `apply:`):\n  " + "\n  ".join(offenders))


def _valid_include_keywords():
    """Keys ansible allows directly on an include_tasks task.

    Mirrors ansible's TaskInclude.VALID_INCLUDE_KEYWORDS, with a hardcoded
    fallback if ansible is unavailable in the test environment.
    """
    try:
        from ansible.playbook.task_include import TaskInclude
        valid = set(TaskInclude.VALID_INCLUDE_KEYWORDS)
    except Exception:
        valid = {
            "action", "args", "collections", "debugger", "ignore_errors",
            "loop", "loop_control", "loop_with", "name", "no_log", "register",
            "run_once", "tags", "timeout", "vars", "when", "apply",
        }
    return valid


def test_include_tasks_use_only_valid_keywords():
    """No include_tasks task may use a keyword outside VALID_INCLUDE_KEYWORDS.

    This catches the whole class of include-keyword bugs (not just
    ``failed_when``): any reserved keyword placed on an include is either inert
    or aborts the play. ``rx_*`` keys are resilient_exec passthrough vars set at
    the include-task level (e.g. roles/orchestrator/tasks/helm_uninstall.yml)
    and are legitimate, so they are excluded.
    """
    valid = _valid_include_keywords()
    offenders = []
    for path in _task_files():
        with open(path) as f:
            try:
                doc = yaml.safe_load(f)
            except yaml.YAMLError:
                continue
        if not isinstance(doc, list):
            continue
        for task in _flatten(doc):
            if not any(k in task for k in INCLUDE_KEYS):
                continue
            for key in task:
                if key in INCLUDE_KEYS:
                    continue
                if key in valid:
                    continue
                if key.startswith("rx_"):
                    continue
                rel = os.path.relpath(path, REPO_ROOT)
                offenders.append("%s: %s (key %r)" % (rel, task.get("name", "?"), key))
    assert not offenders, (
        "include_tasks uses a keyword outside TaskInclude.VALID_INCLUDE_KEYWORDS "
        "(catches the whole class of include-keyword bugs, not just "
        "failed_when):\n  " + "\n  ".join(offenders))
