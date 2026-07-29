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


def test_conflicts_are_split_three_ways():
    task = _named(_explainer(), "Classify the conflicted paths")
    facts = task["ansible.builtin.set_fact"]
    for fact in ("_pull_conflict_encrypted", "_pull_conflict_binary",
                 "_pull_conflict_plain"):
        assert fact in facts, "missing bucket: %s" % fact
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


def test_encrypted_conflicts_warn_against_editing_in_place():
    msg = _fail_msg()
    assert "_pull_conflict_encrypted" in msg
    assert "ciphertext" in msg, "say why git cannot merge them"
    # git leaves no markers and checks out one side decrypted, so the file
    # looks resolvable and editing it drops the other host's change.
    flat = " ".join(msg.split())
    assert "writes no conflict markers" in flat
    assert "one side only" in flat


def test_encrypted_conflicts_offer_a_real_three_way_merge():
    msg = _fail_msg()
    # Both hosts may have edited different parts of the same file; taking one
    # side wholesale would be data loss, so a real merge has to come first.
    assert "git merge-file" in msg, "a true merge must be the primary route"
    assert "git-crypt smudge" in msg, "the stages have to be decrypted first"
    for stage in (":1:", ":2:", ":3:", '"$d/base"', '"$d/remote"', '"$d/local"'):
        assert stage in msg, "missing stage handling: %s" % stage
    # Named scratch files, not :1:/:2:/:3: positional temp names — an operator
    # who mixes up the stages merges the wrong pair.
    assert '"$d/merged"' in msg, "the file to edit and install must be named"
    assert "base is the common ancestor" in msg


def test_decrypted_scratch_copies_are_flagged():
    msg = _fail_msg()
    assert "rm -rf" in msg and "decrypted secrets" in msg, (
        "the recipe writes cleartext credentials to disk; say so and clean up"
    )


def test_taking_one_side_stays_available_as_the_shortcut():
    msg = _fail_msg()
    assert "--ours" in msg and "--theirs" in msg
    assert "remote's version" in msg, (
        "ours/theirs invert under rebase; label them rather than assume"
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


def test_tracked_binaries_are_not_called_mergeable():
    # A public_assets logo is as unmergeable as ciphertext but has nothing to
    # decrypt; calling it "resolve the usual way" sends the operator to edit a
    # PNG in a text editor.
    task = _named(_explainer(), "Collect conflicted paths before the rebase is rolled back")
    cmd = task["ansible.builtin.shell"]["cmd"]
    assert "tr -d '\\000'" in cmd, "expected a NUL-byte test to spot binaries"
    msg = _fail_msg()
    assert "_pull_conflict_binary" in msg
    assert "are binary, so git cannot merge them either" in msg


def test_encrypted_paths_the_merge_driver_handled_are_not_given_the_long_recipe():
    # With a merge driver registered, the driver has already decrypted,
    # merged and re-encrypted the path, so the working tree holds an ordinary
    # marker conflict. Printing the decrypt-the-stages recipe for it tells the
    # operator to redo work that is done.
    task = _named(_explainer(), "Collect conflicted paths before the rebase is rolled back")
    cmd = task["ansible.builtin.shell"]["cmd"]
    assert "check-attr merge" in cmd, (
        "encryption alone does not decide the advice; whether a driver ran does"
    )
    assert 'git config --get "merge.$m.driver"' in cmd, (
        "the attribute can name a driver this host has not registered, and "
        "then git falls back to the binary conflict the long recipe is for"
    )
    # Both conditions, not either: named AND registered.
    assert '[ "$m" != "unspecified" ]' in cmd


def test_the_long_recipe_survives_for_hosts_without_the_driver():
    cmd = _named(_explainer(), "Collect conflicted paths before the rebase is rolled back")["ansible.builtin.shell"]["cmd"]
    assert "printf 'encrypted" in cmd, (
        "a host on an older CLI, or one where git-crypt is unavailable and "
        "the driver failed closed, still needs the full recipe"
    )
    assert "git merge-file" in _fail_msg()
