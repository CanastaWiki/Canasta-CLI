"""Structural tests for K8s backup-scheduling tasks.

These exercise YAML structure rather than runtime behavior — the
playbook file is parsed, the CronJob block is located, and we
assert that field-name conventions and K8s-side defaults are in
place. They guard against regressions that show up only in
production (slow failure feedback, missing history, etc.).

End-to-end behavior is covered separately on a real cluster (see
issue #424).
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SCHEDULE_SET = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "schedule_set.yml",
)


def _load_tasks():
    with open(SCHEDULE_SET) as f:
        return yaml.safe_load(f)


def _walk_tasks(tasks):
    """Yield every task dict, descending into block/rescue/always."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _find_cronjob_definition():
    """Find the kubernetes.core.k8s task that creates the CronJob and
    return its `definition` mapping.

    schedule_set.yml has one CronJob-creating task; any future task
    that also creates a CronJob would need its own assertion. The
    `state: present` filter rules out the schedule_remove task that
    deletes the same resource.
    """
    for task in _walk_tasks(_load_tasks()):
        k8s = task.get("kubernetes.core.k8s") or task.get("k8s")
        if not isinstance(k8s, dict):
            continue
        defn = k8s.get("definition") or {}
        if defn.get("kind") == "CronJob" and k8s.get("state") == "present":
            return defn
    raise AssertionError(
        "schedule_set.yml has no `state: present` CronJob task — "
        "test environment expected one"
    )


class TestCronJobHistoryLimits:
    """Both successful and failed history limits must be set on the
    backup CronJob. K8s defaults (3 successful / 1 failed) silently
    rotate failure context off the cluster faster than an operator
    notices — see #499.
    """

    def test_successful_jobs_history_limit_set(self):
        spec = _find_cronjob_definition()["spec"]
        assert "successfulJobsHistoryLimit" in spec, (
            "CronJob.spec.successfulJobsHistoryLimit is missing — set "
            "an explicit value rather than relying on the K8s default."
        )
        assert isinstance(spec["successfulJobsHistoryLimit"], int)
        assert spec["successfulJobsHistoryLimit"] > 0

    def test_failed_jobs_history_limit_set(self):
        spec = _find_cronjob_definition()["spec"]
        assert "failedJobsHistoryLimit" in spec, (
            "CronJob.spec.failedJobsHistoryLimit is missing — set "
            "an explicit value (the default 1 rotates failure logs "
            "out before the operator notices)."
        )
        assert isinstance(spec["failedJobsHistoryLimit"], int)
        # Failed jobs are MORE valuable than successful ones for
        # post-mortem; expect at least 3.
        assert spec["failedJobsHistoryLimit"] >= 3, (
            "failedJobsHistoryLimit < 3 — keep at least a week of "
            "daily failures around for post-mortem."
        )

    def test_concurrency_policy_forbid(self):
        """A long-running backup must not double-fire — Forbid skips
        the next firing if the previous Job is still running."""
        spec = _find_cronjob_definition()["spec"]
        assert spec.get("concurrencyPolicy") == "Forbid", (
            "CronJob.spec.concurrencyPolicy must be 'Forbid' so a "
            "long-running backup doesn't get a parallel firing."
        )
