"""Compose submodule checkouts authenticate like the superproject.

Pull, join and fix-submodules fetch extension submodules that may live in
private repos reached over SSH, so `git submodule update` has to run with
the same GIT_SSH_COMMAND (and so the same --ssh-key) as the clone or pull.
"""

import os

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")


def _walk(items):
    for item in items or []:
        if not isinstance(item, dict):
            continue
        yield item
        for key in ("block", "rescue", "always"):
            yield from _walk(item.get(key))


def _tasks(name):
    with open(os.path.join(TASKS, name)) as f:
        return list(_walk(yaml.safe_load(f)))


def test_pull_and_join_pass_the_git_env_to_submodule_updates():
    for name in ("pull_compose.yml", "join.yml"):
        includes = [t for t in _tasks(name)
                    if "_update_submodules.yml"
                    in str(t.get("ansible.builtin.include_tasks", ""))]
        assert includes, name
        for t in includes:
            assert (t.get("vars") or {}).get("submodule_git_env") \
                == "{{ gitops_git_env }}", (name, t.get("name"))


def test_fix_submodules_passes_the_git_env():
    updates = [t for t in _tasks("fix_submodules.yml")
               if "submodule update" in str(
                   (t.get("ansible.builtin.command") or {}).get("cmd", ""))]
    assert updates
    for t in updates:
        assert t.get("environment") == "{{ gitops_git_env }}", t.get("name")
