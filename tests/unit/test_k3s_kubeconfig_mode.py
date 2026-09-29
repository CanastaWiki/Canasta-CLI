"""The k3s admin kubeconfig is readable by the SSH user's group, not by all.

New control planes are installed with --write-kubeconfig-mode 0640 and
--write-kubeconfig-group; `canasta upgrade` edits the unit of an existing
control plane from 0644 to the same settings.
"""

import os
import re

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
INSTALL = os.path.join(TASKS, "k8s_install_k3s.yml")
MIGRATION = os.path.join(TASKS, "k8s_kubeconfig_mode_upgrade.yml")
UPGRADE = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")

UNIT_HEAD = (
    "ExecStart=/usr/local/bin/k3s \\\n"
    "    server \\\n"
    "\t'--write-kubeconfig-mode' \\\n"
    "\t'0644' \\\n"
)


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _by_name(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
        found = _by_name(task.get("block", []), name) if "block" in task else None
        if found:
            return found
    return None


def _migrate(unit, group="canasta"):
    task = _by_name(_load(MIGRATION),
                    "Make the k3s unit write the kubeconfig as 0640")
    args = task["ansible.builtin.replace"]
    replace = args["replace"].replace("{{ _kcm_group.stdout }}", group)
    # ansible.builtin.replace compiles the pattern with re.MULTILINE.
    return re.sub(args["regexp"], replace, unit, flags=re.MULTILINE)


def test_install_writes_kubeconfig_0640_with_group():
    exec_args = _by_name(_load(INSTALL), "Build k3s install exec args")
    value = exec_args["ansible.builtin.set_fact"]["_k3s_install_exec"]
    assert "--write-kubeconfig-mode 0640" in value
    assert "--write-kubeconfig-group" in value
    assert "0644" not in value


def test_install_chmod_matches_the_unit():
    chmod = _by_name(_load(INSTALL),
                     "Make k3s kubeconfig readable (before verification)")
    args = chmod["ansible.builtin.file"]
    assert args["mode"] == "0640"
    assert args["group"] == "{{ _k3s_kubeconfig_group.stdout }}"


@pytest.mark.parametrize("tail", [
    "\n",
    "\t'--tls-san' \\\n\t'203.0.113.10' \\\n\n",
])
def test_migration_rewrites_the_unit(tail):
    out = _migrate(UNIT_HEAD + tail)
    assert out == (
        "ExecStart=/usr/local/bin/k3s \\\n"
        "    server \\\n"
        "\t'--write-kubeconfig-mode' \\\n"
        "\t'0640' \\\n"
        "\t'--write-kubeconfig-group' \\\n"
        "\t'canasta' \\\n"
    ) + tail


def test_migration_is_idempotent():
    once = _migrate(UNIT_HEAD + "\n")
    assert _migrate(once) == once


def test_migration_restarts_only_on_change():
    tasks = _load(MIGRATION)
    for name in ("Restart k3s to rewrite the kubeconfig",
                 "Wait for k3s after the restart"):
        assert _by_name(tasks, name)["when"] == "_kcm_edit is changed"


def test_upgrade_runs_the_migration_for_kubernetes():
    task = _by_name(_load(UPGRADE), "Restrict the k3s kubeconfig (Kubernetes)")
    assert task["ansible.builtin.include_tasks"].strip().endswith(
        "roles/orchestrator/tasks/k8s_kubeconfig_mode_upgrade.yml")
    assert "kubernetes" in task["when"]
