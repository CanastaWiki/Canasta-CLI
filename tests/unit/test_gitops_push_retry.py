"""gitops push (K8s) must retry a commit left unpushed by an earlier failed
push, not only push when new changes were staged this run."""
import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PUSHK8S = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push_kubernetes.yml")


def _read(p):
    with open(p) as fh:
        return fh.read()


def _tasks():
    return yaml.safe_load(_read(PUSHK8S))


def test_commit_is_gated_on_staged_changes():
    commit = [t for t in _tasks() if (t.get("name") or "") == "Commit staged changes"]
    assert commit, "expected a 'Commit staged changes' task"
    assert "_git_staged.rc != 0" in str(commit[0].get("when"))


def test_push_is_gated_on_unpushed_commits_not_staged():
    c = _read(PUSHK8S)
    # Unpushed commits are counted against the upstream and drive the push, so
    # a commit stranded by a prior failed push is retried.
    assert "git rev-list --count @{u}..HEAD" in c
    push = [t for t in _tasks() if (t.get("name") or "") == "Push to remote"]
    assert push, "expected a 'Push to remote' task"
    when = str(push[0].get("when"))
    assert "_unpushed" in when and "int) > 0" in when
    # The push must NOT be gated on freshly-staged changes alone.
    assert "_git_staged" not in when
