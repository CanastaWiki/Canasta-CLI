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
