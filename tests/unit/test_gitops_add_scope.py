"""Regression guard for 'canasta gitops add' staging scope.

'gitops add' now requires explicit file arguments and stages only those
paths. It must never fall back to a whole-tree `git add -A` (which, since
Git 2.0, ignores chdir and stages extensions/, host files, public_assets/,
etc. regardless of pathspec). This test asserts add.yml carries no
`git add -A` form, so a reintroduced wildcard stage is caught.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ADD_YML = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "add.yml")


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _load_tasks():
    with open(ADD_YML) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


def _cmd(task):
    c = task.get("ansible.builtin.command") or task.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


class TestGitopsAddScope:
    def test_no_whole_tree_add(self):
        """add.yml must never use a whole-tree `git add -A`. Staging is
        always to explicit, named paths."""
        offenders = [
            _cmd(t) for t in _load_tasks() if "git add -A" in _cmd(t)
        ]
        assert not offenders, (
            "gitops add must stage explicit paths, not whole-tree "
            "`git add -A` (chdir-ignored since Git 2.0); found: %r" % offenders
        )

    def test_requested_files_loop_is_non_fatal(self):
        """The per-file `git add` loop must not abort on a bad path with
        the opaque generic 'non-zero return code'. It carries
        `failed_when: false` so the follow-up fail task can emit a legible
        message instead."""
        loop_task = next(
            t for t in _load_tasks()
            if "git add -- {{ item }}" in _cmd(t)
        )
        assert loop_task.get("failed_when") is False, (
            "the requested-files git add loop must set failed_when: false "
            "so a rejected path yields a clear message, not 'non-zero "
            "return code'"
        )

    def test_clear_failure_message_task_present(self):
        """A fail task must surface rejected paths with git's own reason
        and the instance-root-relative reminder."""
        fail_tasks = [
            t for t in _load_tasks()
            if "ansible.builtin.fail" in t or "fail" in t
        ]
        msgs = []
        for t in fail_tasks:
            f = t.get("ansible.builtin.fail") or t.get("fail") or {}
            if isinstance(f, dict):
                msgs.append(f.get("msg", ""))
        joined = "\n".join(msgs)
        assert "relative to the" in joined and "instance root" in joined, (
            "add.yml must fail with a message reminding that paths are "
            "relative to the instance root"
        )
