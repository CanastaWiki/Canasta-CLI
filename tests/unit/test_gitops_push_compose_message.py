"""Compose `gitops push` must commit the operator's --message verbatim.

A message built into a command string splits on its quotes and has
$WORDs expanded, so both commit tasks (direct and pull-request mode) and
the pull request title pass it as a single argv element.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PUSH_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml")


def _block(name):
    with open(PUSH_COMPOSE) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in tasks if t.get("name") == name)["block"]


def _task(block_name, task_name):
    return next(t for t in _block(block_name) if t.get("name") == task_name)


COMMIT_TASKS = [
    ("Direct push to main", "Commit staged changes"),
    ("Push via pull request", "Commit staged changes"),
]


@pytest.mark.parametrize("block_name,task_name", COMMIT_TASKS)
class TestComposeCommitMessage:
    def test_the_message_option_is_used(self, block_name, task_name):
        argv = _task(block_name, task_name)["ansible.builtin.command"]["argv"]
        assert argv[-2] == "-m"
        assert "message" in argv[-1]

    def test_it_falls_back_when_the_message_is_empty(
            self, block_name, task_name):
        msg = _task(block_name, task_name)["ansible.builtin.command"]["argv"][-1]
        assert "default('Configuration update', true)" in msg

    def test_the_message_is_one_argument_taken_verbatim(
            self, block_name, task_name):
        cmd = _task(block_name, task_name)["ansible.builtin.command"]
        assert "cmd" not in cmd
        assert cmd["expand_argument_vars"] is False

    def test_commit_signing_stays_off(self, block_name, task_name):
        argv = _task(block_name, task_name)["ansible.builtin.command"]["argv"]
        assert argv[:4] == ["git", "-c", "commit.gpgsign=false", "commit"]


class TestPullRequestTitle:
    def test_the_title_is_one_argument_taken_verbatim(self):
        cmd = _task("Push via pull request", "Create pull request")[
            "ansible.builtin.command"]
        assert "cmd" not in cmd
        assert cmd["expand_argument_vars"] is False
        argv = cmd["argv"]
        title = argv[argv.index("--title") + 1]
        assert "default('Configuration update', true)" in title
