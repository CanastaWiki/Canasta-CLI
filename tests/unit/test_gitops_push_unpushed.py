"""Regression guards for 'canasta gitops push'.

push_compose.yml used to end the play whenever nothing was staged,
so a commit already made by another command (e.g. 'canasta gitops
fix-submodules', which commits the .gitmodules registration directly)
could never be pushed — the operator had to fall back to a bare
'git push origin main'.

These tests parse push_compose.yml and assert that push fires on
unpushed commits as well as staged changes, and that the commit step
is guarded so it never runs against an empty index.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PUSH_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml",
)


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _load_tasks():
    with open(PUSH_COMPOSE) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


def _when_str(task):
    when = task.get("when", "")
    if isinstance(when, list):
        return " ".join(str(c) for c in when)
    return str(when)


def _task_by_name(tasks, name):
    return [t for t in tasks if t.get("name") == name]


class TestPushPublishesUnpushedCommits:
    def test_counts_unpushed_commits(self):
        """An explicit task must count commits ahead of the upstream so
        push isn't gated solely on the staging area."""
        tasks = _load_tasks()
        cmds = [
            (t.get("ansible.builtin.command") or t.get("command") or {})
            for t in tasks
        ]
        cmds = [c.get("cmd", "") if isinstance(c, dict) else str(c) for c in cmds]
        assert any(
            "rev-list" in c and "@{upstream}..HEAD" in c for c in cmds
        ), "push_compose.yml must count unpushed commits via git rev-list @{upstream}..HEAD"

    def test_end_play_considers_unpushed_not_just_staged(self):
        """The early-exit must require BOTH no staged changes AND no
        unpushed commits — otherwise an already-made commit is stranded."""
        tasks = _load_tasks()
        end_tasks = [
            t for t in tasks
            if (t.get("ansible.builtin.meta") or t.get("meta")) == "end_play"
        ]
        assert end_tasks, "push_compose.yml lost its end_play guard"
        for t in end_tasks:
            when = _when_str(t)
            assert "_push_has_unpushed" in when and "_push_has_staged" in when, (
                "end_play must gate on both _push_has_staged and "
                "_push_has_unpushed, not staged changes alone"
            )

    def test_commit_steps_are_guarded_on_staged(self):
        """'Commit staged changes' must only run when something is
        staged; otherwise an unpushed-only push commits an empty index
        and fails."""
        tasks = _load_tasks()
        commit_tasks = _task_by_name(tasks, "Commit staged changes")
        assert commit_tasks, "push_compose.yml lost its commit task(s)"
        for t in commit_tasks:
            assert "_push_has_staged" in _when_str(t), (
                "each 'Commit staged changes' task must be guarded by "
                "when: _push_has_staged"
            )
