"""`gitops push` must not rewrite hosts/_shared/vars.yaml from a stale base.

.gitattributes encrypts hosts/** with git-crypt and declares no merge driver,
so git merges the stored ciphertext. A shared-key migration performed while the
host is behind the remote therefore produces a commit that can never be
replayed on top of another host's version of the same file, and `gitops pull`
aborts with a binary conflict the CLI offers no way through.

Deferring the migration — rather than failing the push — is what keeps the
operator unstuck: push is the only command that commits, and pull refuses to
run on a dirty tree, so failing here would strand local edits.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PUSH_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml",
)


def _read():
    with open(PUSH_COMPOSE) as fh:
        return fh.read()


def _tasks():
    return yaml.safe_load(_read())


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _named(name):
    for task in _walk(_tasks()):
        if (task.get("name") or "") == name:
            return task
    return None


def test_push_fetches_before_deciding():
    task = _named("Fetch the remote to compare against")
    assert task, "expected a fetch before the shared-key migration"
    cmd = task["ansible.builtin.command"]["cmd"]
    assert "git fetch" in cmd
    # Must not fail the push when the remote is unreachable.
    assert task.get("failed_when") is False
    assert task.get("changed_when") is False


def test_behind_count_is_measured_against_the_upstream():
    task = _named("Count commits this host is missing")
    assert task, "expected a behind-count task"
    cmd = task["ansible.builtin.command"]["cmd"]
    assert "rev-list --count HEAD..@{upstream}" in cmd
    assert task.get("failed_when") is False


def test_unreachable_remote_is_treated_as_up_to_date():
    task = _named("Record whether this host is behind the remote")
    assert task, "expected the behind fact to be set"
    expr = str(task["ansible.builtin.set_fact"]["_push_is_behind"])
    # Both the fetch and the count must have succeeded before a non-zero
    # count is believed, so an offline host keeps the old behavior.
    assert "_push_fetch.rc" in expr
    assert "_push_behind.rc" in expr
    assert "int) > 0" in expr


def test_migration_is_skipped_while_behind():
    task = _named("Write migrated shared vars")
    assert task, "expected the shared-vars migration block"
    when = task.get("when")
    assert isinstance(when, list), "expected the guard to be a condition list"
    assert any("_push_keys_to_migrate" in str(c) for c in when)
    assert any(
        "not" in str(c) and "_push_is_behind" in str(c) for c in when
    ), "migration must not run while the host is behind the remote"


def test_skipping_is_reported_to_the_operator():
    task = _named("Defer the migration while this host is behind")
    assert task, "a silently skipped migration would look like a no-op"
    msg = task["ansible.builtin.debug"]["msg"]
    assert "gitops pull" in msg, "the message must name the way forward"


def test_push_itself_is_not_blocked_by_being_behind():
    # The commit and push must stay reachable so staged work still lands
    # locally; the remote's own rejection routes the operator to pull.
    for name in ("Commit staged changes", "Push to main"):
        task = _named(name)
        assert task, "expected a '%s' task" % name
        assert "_push_is_behind" not in str(task.get("when", "")), (
            "%s must not be gated on being behind" % name
        )
