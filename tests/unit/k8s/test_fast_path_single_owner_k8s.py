"""`gitops diff` runs on the fast path for Compose, so its Ansible path
refuses anything but a Kubernetes instance and dispatches only to
diff_kubernetes.yml."""

import os

import yaml

from test_fast_path_single_owner import REPO_ROOT


def test_gitops_diff_ansible_path_is_kubernetes_only():
    tasks_dir = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
    assert not os.path.exists(os.path.join(tasks_dir, "diff_compose.yml"))
    with open(os.path.join(tasks_dir, "diff.yml")) as f:
        tasks = yaml.safe_load(f)
    guard = next(t for t in tasks if "ansible.builtin.fail" in t)
    assert guard["when"] == "not (instance_is_k8s | bool)"
    includes = [t["ansible.builtin.include_tasks"] for t in tasks
                if "ansible.builtin.include_tasks" in t]
    assert "diff_kubernetes.yml" in includes
