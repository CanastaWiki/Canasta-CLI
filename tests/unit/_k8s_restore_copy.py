"""Resolve a K8s restore task that copies a directory through
_k8s_restore_copy_dir.yml into the shell task it runs, with the include's
copy_* vars substituted into the command, so structural tests can assert on
the command each copy actually runs."""

import copy
import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COPY_DIR = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "_k8s_restore_copy_dir.yml")


def resolve_copy_task(task):
    """Return the task unchanged unless it includes the copy helper; then
    return the helper's shell task with the include's when and vars applied."""
    if task.get("ansible.builtin.include_tasks") != "_k8s_restore_copy_dir.yml":
        return task
    with open(COPY_DIR) as f:
        (shell_task,) = yaml.safe_load(f)
    resolved = copy.deepcopy(shell_task)
    variables = task.get("vars", {})
    cmd = resolved["ansible.builtin.shell"]["cmd"]
    cmd = cmd.replace(
        "{{ copy_tar_extract_args | default('') }}",
        variables.get("copy_tar_extract_args", ""))
    cmd = cmd.replace("{{ copy_src }}", variables["copy_src"])
    cmd = cmd.replace("{{ copy_dest }}", variables["copy_dest"])
    resolved["ansible.builtin.shell"]["cmd"] = cmd
    resolved["name"] = task["name"]
    if "when" in task:
        resolved["when"] = task["when"]
    return resolved
