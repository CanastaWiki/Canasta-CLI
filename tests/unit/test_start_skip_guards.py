"""Guard against register-on-skip in start.yml's failure diagnostics.

`Start containers` is skipped when the instance is already running, and a
skipped task registers a dict with no `rc` key at all. The three failure
guards that read `_start_result.rc` must therefore default it, or every
`canasta start` / `canasta reconcile` against a running instance dies with
"object of type 'dict' has no attribute 'rc'" before reaching its own work.
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
START = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _all_tasks():
    with open(START) as f:
        return list(_walk(yaml.safe_load(f)))


class TestStartResultGuards:
    def test_start_containers_is_conditional(self):
        """The premise: the registering task can be skipped."""
        task = next(
            t for t in _all_tasks() if t.get("name") == "Start containers"
        )
        assert task.get("register") == "_start_result"
        assert "_container_running" in str(task.get("when", "")), (
            "start is skipped on an already-running instance — that is what "
            "makes the guards below load-bearing"
        )

    def test_every_rc_conditional_defaults(self):
        bare = [
            t.get("name")
            for t in _all_tasks()
            if "_start_result.rc" in str(t.get("when", ""))
            and "_start_result.rc | default" not in str(t.get("when", ""))
        ]
        assert not bare, (
            "these tasks read _start_result.rc without a default and will "
            f"raise when the start was skipped: {bare}"
        )

    def test_the_guards_still_exist(self):
        """The diagnostics stay; only their conditional was wrong."""
        guarded = [
            t.get("name")
            for t in _all_tasks()
            if "_start_result.rc" in str(t.get("when", ""))
        ]
        assert len(guarded) == 3, (
            "expected the ps capture, the logs capture, and the explicit "
            f"fail to remain gated on rc; found {guarded}"
        )
