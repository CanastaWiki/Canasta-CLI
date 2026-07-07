"""Guard: gitops init doesn't print its failure message twice.

init.yml wraps init in a block/rescue and re-raises so the play exits non-zero.
Ansible already prints the failed task's error, so the re-raise must NOT embed
`ansible_failed_result.msg` — doing so printed the same error a second time.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INIT = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "init.yml")


def _flatten(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            if key in t:
                yield from _flatten(t[key])


def _reraise_msg():
    with open(INIT) as f:
        tasks = list(_flatten(yaml.safe_load(f)))
    t = next(t for t in tasks if t.get("name") == "Re-raise the init failure")
    return t["ansible.builtin.fail"]["msg"]


def test_reraise_does_not_repeat_the_original_message():
    msg = _reraise_msg()
    assert "ansible_failed_result.msg" not in msg, (
        "re-raise must not embed the failed task's message (it's already "
        "printed) — that is what caused the duplicate error output")
    assert "see the error above" in msg
