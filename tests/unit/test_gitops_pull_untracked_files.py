"""`canasta gitops pull` must not block on untracked files.

The dirty-tree guard exists so a pull cannot overwrite a locally modified
tracked file. Untracked files carry no such hazard — git refuses a genuine
collision on its own — and blocking on them made the pull unrunnable on any
host carrying a local settings override or scratch file. The old advice was
also a dead end: plain `git stash` does not stash untracked files, so
following it changed nothing and the operator looped.
"""

import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PULL_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml"
)


def _tasks():
    with open(PULL_COMPOSE) as fh:
        return yaml.safe_load(fh)


def _guard():
    for t in _tasks():
        cmd = (t.get("ansible.builtin.command") or {}).get("cmd", "")
        if cmd.startswith("git status --porcelain"):
            return t
    return None


def test_dirty_tree_guard_ignores_untracked_files():
    guard = _guard()
    assert guard is not None, "expected the pre-pull dirty-tree check"
    cmd = guard["ansible.builtin.command"]["cmd"]
    assert "--untracked-files=no" in cmd, (
        "the pull guard must ignore untracked files; bare "
        "`git status --porcelain` reports '??' entries and blocks the pull"
    )


def test_failure_message_names_a_command_that_resolves_the_state():
    fail = next(
        (t for t in _tasks() if "ansible.builtin.fail" in t
         and "Cannot pull" in str(t["ansible.builtin.fail"].get("msg", ""))),
        None,
    )
    assert fail is not None, "expected the dirty-tree failure task"
    msg = " ".join(str(fail["ansible.builtin.fail"]["msg"]).split())
    assert "canasta gitops add" in msg and "canasta gitops push" in msg, (
        "the message must point at the canasta commands that share the change"
    )
    # `git stash` is a no-op on the state the old message reported.
    assert "stash" not in msg
