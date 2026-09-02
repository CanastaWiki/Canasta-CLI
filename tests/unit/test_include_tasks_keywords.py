"""Guards around `include_tasks` keyword usage.

Two structural checks over every task file:

1. `test_no_inert_ignore_errors_on_include_tasks` — `ignore_errors: true` placed
   directly on a dynamic `include_tasks` is inert (it applies to the include
   operation, not the tasks inside), so a non-zero result there aborts the whole
   play. `canasta remove` actually broke this way (#1058, 10eb627); the correct
   form puts the flag under the include's `apply:`.

2. `test_include_tasks_use_only_valid_keywords` — no `include_tasks` task may
   use a keyword outside `TaskInclude.VALID_INCLUDE_KEYWORDS`. Any reserved
   keyword placed on an include is either inert or a hard failure, the same
   class of bug as `failed_when` / top-level `ignore_errors`.
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

    Mirrors ansible's TaskInclude.VALID_INCLUDE_KEYWORDS. Imported directly so
    any failure surfaces rather than being approximated by a fallback.
    """
    from ansible.playbook.task_include import TaskInclude
    return set(TaskInclude.VALID_INCLUDE_KEYWORDS)


def test_include_tasks_use_only_valid_keywords():
    """No include_tasks task may use a keyword outside VALID_INCLUDE_KEYWORDS.

    This catches the whole class of include-keyword bugs (not just
    ``failed_when``): any reserved keyword placed on an include is either inert
    or aborts the play. ``rx_*`` resilient_exec passthrough vars always live
    under the include's ``vars:`` block, so they are never top-level keys and
    this guard naturally covers a stray top-level ``rx_fail`` — the same hard
    TaskInclude-attribute failure as ``failed_when``.
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
                rel = os.path.relpath(path, REPO_ROOT)
                offenders.append("%s: %s (key %r)" % (rel, task.get("name", "?"), key))
    assert not offenders, (
        "include_tasks uses a keyword outside TaskInclude.VALID_INCLUDE_KEYWORDS "
        "(catches the whole class of include-keyword bugs, not just "
        "failed_when):\n  " + "\n  ".join(offenders))
