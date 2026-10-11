"""Deleting the restic Job must take its pod with it.

kubernetes.core.k8s sends no propagation policy by default, and the API
orphans a batch/v1 Job's pods in that case.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
RUN_BACKUP = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                          "k8s_run_backup.yml")


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def test_every_job_delete_propagates_to_pods():
    with open(RUN_BACKUP) as f:
        tasks = list(_walk(yaml.safe_load(f)))
    deletes = [
        t["kubernetes.core.k8s"] for t in tasks
        if t.get("kubernetes.core.k8s", {}).get("state") == "absent"
        and t["kubernetes.core.k8s"].get("kind") == "Job"
    ]
    assert deletes
    for module in deletes:
        assert module.get("delete_options", {}).get(
            "propagationPolicy") in ("Background", "Foreground")
