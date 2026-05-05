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


class TestBackupEnvSecret:
    """Both the init container (DB dump) and the restic container in
    the scheduled CronJob must envFrom the same canasta-<id>-backup-
    env Secret. That Secret is the only path for cloud-backend creds
    (#497) and external-DB host/user (#498) to reach the Job."""

    @staticmethod
    def _pod_template(spec):
        return spec["jobTemplate"]["spec"]["template"]["spec"]

    def _expected_secret_name(self):
        return 'canasta-{{ instance_id }}-backup-env'

    def test_dump_databases_init_container_envfroms_backup_secret(self):
        spec = _find_cronjob_definition()["spec"]
        init = self._pod_template(spec)["initContainers"][0]
        assert init["name"] == "dump-databases"
        env_from = init.get("envFrom") or []
        assert env_from, (
            "dump-databases initContainer must envFrom the backup-env "
            "Secret — without it MYSQL_HOST/USER fall back to in-script "
            "defaults and external-DB instances fail (#498)."
        )
        names = [e.get("secretRef", {}).get("name") for e in env_from]
        assert self._expected_secret_name() in names

    def test_restic_container_envfroms_backup_secret(self):
        spec = _find_cronjob_definition()["spec"]
        containers = self._pod_template(spec)["containers"]
        restic = next(c for c in containers if c["name"] == "restic")
        env_from = restic.get("envFrom") or []
        assert env_from, (
            "restic container must envFrom the backup-env Secret — "
            "without it cloud backends (S3/B2/Azure/GCS) can't "
            "authenticate (#497)."
        )
        names = [e.get("secretRef", {}).get("name") for e in env_from]
        assert self._expected_secret_name() in names

    def test_no_inline_restic_env_anymore(self):
        """The pre-fix layout passed RESTIC_REPOSITORY/PASSWORD as
        inline `env:` entries. After #497 the Secret is the canonical
        source — drift back to inline values would silently re-break
        cloud backends, so guard against it."""
        spec = _find_cronjob_definition()["spec"]
        containers = self._pod_template(spec)["containers"]
        restic = next(c for c in containers if c["name"] == "restic")
        inline_env = restic.get("env") or []
        names = [e.get("name") for e in inline_env]
        for forbidden in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD"):
            assert forbidden not in names, (
                "%s must come from the backup-env Secret, not an "
                "inline env entry (#497)." % forbidden
            )


class TestBackupEnvSecretSyncTask:
    """The Secret is created by k8s_sync_config.yml from the .env
    backup-relevant subset. Verify the task exists and uses the
    expected name + filter."""

    SYNC_PATH = os.path.join(
        REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_sync_config.yml",
    )

    def _load(self):
        with open(self.SYNC_PATH) as f:
            return yaml.safe_load(f)

    def test_apply_secret_task_present(self):
        for task in _walk_tasks(self._load()):
            k8s = task.get("kubernetes.core.k8s") or task.get("k8s")
            if not isinstance(k8s, dict):
                continue
            defn = k8s.get("definition") or {}
            if (defn.get("kind") == "Secret"
                    and "backup-env" in (defn.get("metadata", {}) or {})
                    .get("name", "")):
                return
        raise AssertionError(
            "k8s_sync_config.yml is missing the canasta-<id>-backup-env "
            "Secret task — without it both the on-demand and scheduled "
            "backup paths fail to authenticate to cloud backends."
        )

    def test_filter_covers_known_backup_env_prefixes(self):
        """The filter regex must match every prefix the consumers
        rely on. Keeping the assertion explicit makes a future
        accidental tightening of the filter visible in tests."""
        with open(self.SYNC_PATH) as f:
            content = f.read()
        for prefix_or_key in (
            "RESTIC_", "AWS_", "B2_", "AZURE_", "GOOGLE_", "OS_", "ST_",
            "MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD",
        ):
            assert prefix_or_key in content, (
                "k8s_sync_config.yml's backup-env filter must "
                "cover %r (used by restic backends or the "
                "dump-databases init container)." % prefix_or_key
            )
