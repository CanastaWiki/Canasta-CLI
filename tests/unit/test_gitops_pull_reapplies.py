"""Guard: a gitops pull renders when HEAD's commit was never applied.

A pull that fetched and then failed before rendering leaves the working
tree at the new commit while .env, config/wikis.yaml and my.cnf still hold
the previous values. Deciding whether to render by asking "did this pull
move HEAD" then finds nothing to move and skips forever, so the instance
can never recover — observed in the field, with the only escape being
`git reset --hard` against the instance.

.gitops-applied already records what was last rendered. These tests pin
that the decision is made against it.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml"
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
    with open(PULL_COMPOSE) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


def _cmd(task):
    c = task.get("ansible.builtin.command") or task.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


def _when(task):
    w = task.get("when", [])
    return " ".join(w) if isinstance(w, list) else str(w)


class TestPullRendersWhenNothingWasApplied:
    def test_skip_is_gated_on_the_applied_commit(self):
        """The skip must compare HEAD against .gitops-applied.

        Comparing the pre-pull HEAD against the post-pull HEAD asks whether
        this invocation moved anything, which is not the same question.
        """
        skips = [
            t for t in _load_tasks()
            if t.get("ansible.builtin.meta") == "end_play"
        ]
        assert skips, "the no-op fast path should still exist"

        blocks = [
            t for t in _load_tasks()
            if "_pull_applied" in _when(t) and "_pull_new_commit" in _when(t)
        ]
        assert blocks, (
            "the render skip must be decided against .gitops-applied"
        )

    def test_pre_pull_head_no_longer_decides_the_skip(self):
        """The old comparison is the bug; it must not linger."""
        for t in _load_tasks():
            w = _when(t)
            if "_pull_prev_commit.stdout == _pull_new_commit.stdout" in w:
                assert t.get("ansible.builtin.meta") != "end_play", (
                    "skipping on 'did HEAD move' strands an instance whose "
                    "previous pull failed after fetching"
                )

    def test_changed_files_diff_against_what_was_applied(self):
        """On a re-apply the pre-pull HEAD equals HEAD, so diffing against
        it reports no changed files and the restart decision misses."""
        diffs = [_cmd(t) for t in _load_tasks() if "git diff" in _cmd(t)]
        assert diffs, "the changed-file diff should still exist"
        for d in diffs:
            assert "_pull_diff_base" in d, (
                "diff must be against the last applied commit: %s" % d
            )

    def test_a_missing_or_stale_applied_commit_is_handled(self):
        """.gitops-applied can be absent (never pulled) or name a commit
        that no longer exists (rebased repo). Neither may break the pull."""
        src = open(PULL_COMPOSE).read()
        assert "git cat-file -e" in src, (
            "a stale applied commit must be validated before it is diffed"
        )
        assert "_pull_prev_commit.stdout }}" in src or \
               "else _pull_prev_commit.stdout" in src, (
            "must fall back to the pre-pull HEAD when there is no usable "
            "applied commit"
        )
