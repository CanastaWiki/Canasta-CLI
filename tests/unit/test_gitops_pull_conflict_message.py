"""A failed `gitops pull` must say what blocked it, in terms that apply.

Three different failures reached one message that fit none of them: it told the
operator to resolve a conflict after the rebase had already been rolled back,
it named no files (it pasted git's raw output instead), and it offered a
line-by-line resolution for git-crypt paths, where git compares ciphertext and
no such resolution exists. On Kubernetes the same text also answered a plain
dirty working tree, which is not a conflict at all.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
EXPLAINER = os.path.join(TASKS, "_explain_pull_failure.yml")
PULLS = ("pull_compose.yml", "pull_kubernetes.yml")


def _tasks(path):
    with open(path) as fh:
        return yaml.safe_load(fh) or []


def _explainer():
    return _tasks(EXPLAINER)


def _named(tasks, name):
    for task in tasks:
        if (task.get("name") or "") == name:
            return task
    return None


def _fail_msg():
    for task in _explainer():
        if "ansible.builtin.fail" in task:
            return task["ansible.builtin.fail"]["msg"]
    raise AssertionError("expected a fail task in the explainer")


def test_both_pull_variants_share_the_explainer():
    for name in PULLS:
        raw = open(os.path.join(TASKS, name)).read()
        assert "_explain_pull_failure.yml" in raw, (
            "%s: must route its pull failure through the shared explainer" % name
        )
        assert "_pull_failure" in raw, (
            "%s: must hand the failed result to the explainer" % name
        )


def test_conflicts_are_read_before_the_abort_clears_them():
    tasks = _explainer()
    names = [t.get("name") or "" for t in tasks]
    collect = "Collect conflicted paths before the rebase is rolled back"
    abort = "Abort an incomplete rebase"
    assert collect in names and abort in names
    assert names.index(collect) < names.index(abort), (
        "aborting first would leave nothing to report"
    )


def test_conflicted_paths_are_collected_with_their_filter():
    task = _named(_explainer(), "Collect conflicted paths before the rebase is rolled back")
    cmd = task["ansible.builtin.shell"]["cmd"]
    assert "--diff-filter=U" in cmd, "only unmerged paths are conflicts"
    assert "check-attr filter" in cmd, (
        "the git-crypt attribute decides which advice applies"
    )
    assert task.get("failed_when") is False, (
        "a failure here must not replace the real error"
    )


def test_encrypted_and_plain_conflicts_are_split():
    task = _named(_explainer(), "Classify the conflicted paths")
    facts = task["ansible.builtin.set_fact"]
    assert "git-crypt" in str(facts["_pull_conflict_encrypted"])
    assert "_pull_conflict_plain" in facts
    assert "cannot pull with rebase" in str(facts["_pull_dirty_tree"]), (
        "the dirty-tree refusal must be told apart from a real conflict"
    )


def test_the_abort_still_cannot_fail_the_play():
    task = _named(_explainer(), "Abort an incomplete rebase")
    assert task.get("failed_when") is False, (
        "'no rebase in progress' must not itself fail the play"
    )


def test_dirty_tree_gets_the_add_and_push_route():
    msg = _fail_msg()
    assert "_pull_dirty_tree" in msg
    assert "canasta gitops add" in msg and "canasta gitops push" in msg, (
        "a dirty tree is fixed by sharing or discarding the changes, "
        "not by resolving a conflict"
    )


def test_encrypted_conflicts_get_whole_file_advice():
    msg = _fail_msg()
    assert "_pull_conflict_encrypted" in msg
    assert "ciphertext" in msg, "say why no line-by-line merge exists"
    assert "--theirs" in msg and "--ours" in msg, (
        "taking one side wholesale is the only resolution available"
    )
    assert "is the remote during a rebase" in msg, (
        "ours/theirs invert under rebase; an operator who gets it backwards "
        "silently keeps the wrong credentials"
    )


def test_conflicted_files_are_named():
    msg = _fail_msg()
    assert "for f in _pull_conflict_encrypted" in msg
    assert "for f in _pull_conflict_plain" in msg, (
        "the old message promised files 'named below' and named none"
    )


def test_local_commits_are_still_reported_safe():
    msg = " ".join(_fail_msg().split())
    assert "rolled back" in msg and "intact" in msg
    assert "git reset --hard origin/main" in msg, (
        "the escape hatch must stay documented"
    )
