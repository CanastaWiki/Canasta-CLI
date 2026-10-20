"""A failed `gitops pull` must say what blocked it, in terms that apply.

A dirty working tree gets the add-and-push route. Conflicts are rolled back,
named by kind (edit, or keep one version), and sent to `canasta gitops
resolve`, which replays the pull and stops at them — no raw git, and no
decrypt-by-hand recipe for encrypted files.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
EXPLAINER = os.path.join(TASKS, "_explain_pull_failure.yml")
CLASSIFY = os.path.join(TASKS, "_classify_conflicts.yml")
PULLS = ("pull_compose.yml", "pull_kubernetes.yml")


def _tasks(path):
    with open(path) as fh:
        return yaml.safe_load(fh) or []


def _named(path, name):
    for task in _tasks(path):
        if (task.get("name") or "") == name:
            return task
    return None


def _fail_msg():
    for task in _tasks(EXPLAINER):
        if "ansible.builtin.fail" in task:
            return task["ansible.builtin.fail"]["msg"]
    raise AssertionError("expected a fail task in the explainer")


def _collect_cmd():
    return _named(CLASSIFY, "Collect the conflicted paths")["ansible.builtin.shell"]["cmd"]


def test_both_pull_variants_share_the_explainer():
    for name in PULLS:
        raw = open(os.path.join(TASKS, name)).read()
        assert "_explain_pull_failure.yml" in raw
        assert "_pull_failure" in raw


def test_conflicts_are_read_before_the_abort_clears_them():
    names = [t.get("name") or "" for t in _tasks(EXPLAINER)]
    collect = "Collect conflicted paths before the rebase is rolled back"
    assert _named(EXPLAINER, collect)["ansible.builtin.include_tasks"] == "_classify_conflicts.yml"
    assert names.index(collect) < names.index("Abort an incomplete rebase")


def test_conflicted_paths_are_collected_with_their_filter():
    cmd = _collect_cmd()
    assert "--diff-filter=U" in cmd, "only unmerged paths are conflicts"
    assert "check-attr filter" in cmd
    assert _named(CLASSIFY, "Collect the conflicted paths").get("failed_when") is False


def test_conflicts_are_split_three_ways():
    facts = _named(CLASSIFY, "Classify the conflicted paths")["ansible.builtin.set_fact"]
    for fact in ("_pull_conflict_encrypted", "_pull_conflict_binary", "_pull_conflict_plain"):
        assert fact in facts
    dirty = _named(EXPLAINER, "Note whether a dirty tree blocked the pull")
    assert "cannot pull with rebase" in str(dirty["ansible.builtin.set_fact"]["_pull_dirty_tree"])


def test_an_encrypted_file_with_markers_is_editable_and_one_without_is_not():
    # Decided on the file itself: a driver that failed closed leaves one
    # side and no markers, and calling that editable would drop the other.
    cmd = _collect_cmd()
    assert "grep -q '^<<<<<<< '" in cmd
    assert "printf 'encrypted" in cmd
    assert "git config --get" not in cmd and "check-attr merge" not in cmd


def test_tracked_binaries_are_not_called_mergeable():
    assert "tr -d '\\000'" in _collect_cmd()


def test_the_abort_still_cannot_fail_the_play():
    assert _named(EXPLAINER, "Abort an incomplete rebase").get("failed_when") is False


def test_dirty_tree_gets_the_add_and_push_route():
    msg = _fail_msg()
    assert "_pull_dirty_tree" in msg
    assert "canasta gitops add" in msg and "canasta gitops push" in msg


def test_conflicted_files_are_named_by_what_they_need():
    msg = _fail_msg()
    assert "for f in _pull_conflict_plain" in msg and "(edit to resolve)" in msg
    assert "for f in _pull_conflict_encrypted" in msg and "keep one version" in msg
    assert "for f in _pull_conflict_binary" in msg


def test_local_commits_are_still_reported_safe():
    msg = _fail_msg()
    assert "rolled back" in msg and "intact" in msg


def test_conflicts_are_sent_to_gitops_resolve():
    msg = _fail_msg()
    assert "canasta gitops resolve -i {{ instance_id }}" in msg
    assert "--keep local <file>" in msg and "--keep remote <file>" in msg
    assert "--continue" in msg and "--abort" in msg


def test_no_raw_git_or_decrypt_recipe_is_offered():
    msg = _fail_msg()
    for raw in ("git pull --rebase", "git rebase --continue", "git-crypt smudge",
                "git merge-file", "git checkout --ours"):
        assert raw not in msg, raw
