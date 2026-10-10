"""Kubernetes `gitops push` must commit with the operator's --message.

Compose push uses the message. A hard-coded 'Configuration update' on
Kubernetes gives every push that subject whatever -m says.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PUSH_K8S = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "push_kubernetes.yml")


def _commit_task():
    with open(PUSH_K8S) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in tasks if t.get("name") == "Commit staged changes")


class TestKubernetesCommitMessage:
    def test_the_message_option_is_used(self):
        argv = _commit_task()["ansible.builtin.command"]["argv"]
        assert argv[-2] == "-m"
        assert "message" in argv[-1]

    def test_it_falls_back_when_no_message_is_given(self):
        msg = _commit_task()["ansible.builtin.command"]["argv"][-1]
        assert "default('Configuration update', true)" in msg

    def test_the_message_is_one_argument_taken_verbatim(self):
        # argv, not a command string, so quotes in the message cannot
        # split it; no variable expansion, so a $WORD stays as typed.
        cmd = _commit_task()["ansible.builtin.command"]
        assert "cmd" not in cmd
        assert cmd["expand_argument_vars"] is False

    def test_commit_signing_stays_off(self):
        argv = _commit_task()["ansible.builtin.command"]["argv"]
        assert argv[:4] == ["git", "-c", "commit.gpgsign=false", "commit"]
