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

    def test_init_container_writes_to_writable_dumps_volume(self):
        """The init container's mariadb-dump output must land in a
        writable volume. Previously dumped to /currentsnapshot/config
        which is the read-only ConfigMap mount — every scheduled run
        crashed with "Read-only file system" until the dumps emptyDir
        was added."""
        spec = _find_cronjob_definition()["spec"]
        init = self._pod_template(spec)["initContainers"][0]
        assert init["name"] == "dump-databases"

        mounted = {m["name"] for m in init.get("volumeMounts") or []}
        assert "web-config" not in mounted, (
            "dump-databases initContainer should not mount the "
            "read-only web-config ConfigMap; use the writable dumps "
            "emptyDir for SQL files."
        )
        assert "dumps" in mounted, (
            "dump-databases initContainer must mount the dumps "
            "emptyDir to write SQL files."
        )

        cmd = " ".join(init["command"])
        # The dump path must align with what restore_k8s.yml looks
        # for (it iterates /currentsnapshot/config/backup/db_*.sql).
        # Diverging paths here would make scheduled-snapshot restores
        # silently skip the DB restore step.
        assert "/currentsnapshot/config/backup/" in cmd, (
            "dump-databases script must write to "
            "/currentsnapshot/config/backup/ to match the path "
            "restore_k8s.yml reads from."
        )

    def test_dumps_volume_is_emptydir_at_config_backup_subpath(self):
        """The dumps volume must mount AT /currentsnapshot/config/backup
        in both the init container (writable) and the restic container
        (read-only) so SQL files are captured at the same path the
        on-demand backup path uses — keeps restore_k8s.yml happy with
        a single path."""
        spec = _find_cronjob_definition()["spec"]
        pod = self._pod_template(spec)

        volumes = {v["name"]: v for v in pod["volumes"]}
        assert "dumps" in volumes
        assert "emptyDir" in volumes["dumps"]

        # Init container — writable mount at the canonical dump path
        init = pod["initContainers"][0]
        init_dumps = [m for m in init.get("volumeMounts") or []
                      if m["name"] == "dumps"]
        assert len(init_dumps) == 1
        assert init_dumps[0]["mountPath"] == "/currentsnapshot/config/backup"

        # Restic container — same path, this time read-only
        restic = next(c for c in pod["containers"] if c["name"] == "restic")
        restic_dumps = [m for m in restic.get("volumeMounts") or []
                        if m["name"] == "dumps"]
        assert len(restic_dumps) == 1
        assert restic_dumps[0]["mountPath"] == "/currentsnapshot/config/backup"

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


class TestK8sSecretBackupCapture:
    """Guard #510: canasta-managed K8s Secrets must land in the
    restic snapshot. Without them, a fresh-cluster restore can bring
    back the DB content but no new wiki pod can authenticate against
    it (MYSQL_PASSWORD is in the K8s Secret, not in the DB itself).
    """

    @staticmethod
    def _pod_template(spec):
        return spec["jobTemplate"]["spec"]["template"]["spec"]

    def test_dump_secrets_init_container_present(self):
        spec = _find_cronjob_definition()["spec"]
        init_names = {c["name"]
                      for c in self._pod_template(spec)["initContainers"]}
        assert "dump-secrets" in init_names, (
            "schedule_set.yml CronJob must include a 'dump-secrets' "
            "init container that captures canasta-managed K8s Secrets "
            "into the restic snapshot. Without it, fresh-cluster "
            "restore loses MYSQL_PASSWORD/MW_SECRET_KEY (#510)."
        )

    def test_dump_secrets_writes_to_canonical_path(self):
        spec = _find_cronjob_definition()["spec"]
        init = next(c for c in self._pod_template(spec)["initContainers"]
                    if c["name"] == "dump-secrets")
        cmd = " ".join(init["command"])
        # Path must align with where restore_k8s.yml looks for the
        # secrets file (alongside DB dumps, under the dumps emptyDir).
        assert "/currentsnapshot/config/backup/secrets-" in cmd, (
            "dump-secrets must write to "
            "/currentsnapshot/config/backup/secrets-<id>.yaml "
            "to align with restore_k8s.yml's lookup path."
        )

    def test_dump_secrets_targets_db_credentials_and_mw_secrets(self):
        """Dump by explicit name (not label-selector) so this works
        on instances pre-dating any canasta-managed-by label
        convention."""
        spec = _find_cronjob_definition()["spec"]
        init = next(c for c in self._pod_template(spec)["initContainers"]
                    if c["name"] == "dump-secrets")
        cmd = " ".join(init["command"])
        assert "-db-credentials" in cmd, (
            "dump-secrets must include the <id>-db-credentials Secret "
            "(carries MYSQL_PASSWORD)"
        )
        assert "-mw-secrets" in cmd, (
            "dump-secrets must include the <id>-mw-secrets Secret "
            "(carries MW_SECRET_KEY)"
        )

    def test_dump_secrets_does_not_capture_backup_env(self):
        """The canasta-<id>-backup-env Secret carries the credentials
        needed to READ the snapshot. Including it in the snapshot is
        chicken-and-egg — operator can't get the bootstrap creds
        without the snapshot, can't read the snapshot without the
        creds. Out of scope per #510."""
        spec = _find_cronjob_definition()["spec"]
        init = next(c for c in self._pod_template(spec)["initContainers"]
                    if c["name"] == "dump-secrets")
        cmd = " ".join(init["command"])
        assert "backup-env" not in cmd, (
            "dump-secrets must NOT include the backup-env Secret "
            "(creds-to-read-the-snapshot must not live IN the snapshot)"
        )

    def test_dump_secrets_mounts_dumps_emptydir(self):
        """The init container must mount the same dumps emptyDir the
        SQL dumps go into, so the secrets file ends up in the
        snapshot tree restic backs up."""
        spec = _find_cronjob_definition()["spec"]
        init = next(c for c in self._pod_template(spec)["initContainers"]
                    if c["name"] == "dump-secrets")
        mounts = {m["name"]: m for m in init.get("volumeMounts") or []}
        assert "dumps" in mounts
        assert mounts["dumps"]["mountPath"] == "/currentsnapshot/config/backup"

    def test_pod_uses_dedicated_serviceaccount(self):
        """The dump-secrets init container needs RBAC to read Secrets;
        the default ServiceAccount has no such permission."""
        spec = _find_cronjob_definition()["spec"]
        sa = self._pod_template(spec).get("serviceAccountName")
        assert sa, (
            "Backup CronJob Pod must specify a serviceAccountName — "
            "dump-secrets needs Secret-read RBAC the default SA lacks"
        )
        assert "canasta-backup-" in sa, (
            "ServiceAccount name should be canasta-backup-<id> for "
            "consistency with the Role/RoleBinding created alongside"
        )


class TestK8sSecretBackupRBAC:
    """The CronJob's ServiceAccount must have a Role + RoleBinding
    that grants read on the canasta-managed Secrets. Scoped narrowly:
    only the two Secret names we dump, only `get` verb, namespace-
    local. Least-privilege RBAC."""

    def _find_resources(self, kind):
        for task in _walk_tasks(_load_tasks()):
            k8s = task.get("kubernetes.core.k8s") or task.get("k8s")
            if not isinstance(k8s, dict):
                continue
            defn = k8s.get("definition") or {}
            if defn.get("kind") == kind and k8s.get("state") == "present":
                yield defn

    def test_serviceaccount_created(self):
        sas = list(self._find_resources("ServiceAccount"))
        assert len(sas) >= 1, (
            "schedule_set.yml must create a ServiceAccount for the "
            "backup Pod (#510)"
        )

    def test_role_grants_only_get_on_named_secrets(self):
        roles = list(self._find_resources("Role"))
        assert len(roles) >= 1, (
            "schedule_set.yml must create a Role granting Secret read "
            "to the backup ServiceAccount (#510)"
        )
        rules = roles[0]["rules"]
        # Single rule, get-only, scoped to the two Secret names
        assert len(rules) == 1, (
            "Role should have exactly one rule (least-privilege); "
            "anything else suggests scope creep"
        )
        rule = rules[0]
        assert "secrets" in rule.get("resources", [])
        assert rule.get("verbs", []) == ["get"], (
            "Role must grant only 'get' — list/watch/create/etc. would "
            "exceed what dump-secrets actually needs"
        )
        names = rule.get("resourceNames", [])
        # Dump-secrets reads two Secrets by name
        assert len(names) == 2, (
            "Role should be scoped to exactly the two Secret names "
            "dump-secrets reads — broader scope is unjustified"
        )

    def test_rolebinding_targets_backup_serviceaccount(self):
        rbs = list(self._find_resources("RoleBinding"))
        assert len(rbs) >= 1
        rb = rbs[0]
        subjects = rb.get("subjects", [])
        assert len(subjects) == 1
        assert subjects[0].get("kind") == "ServiceAccount"
        assert "canasta-backup-" in subjects[0].get("name", "")


class TestK8sRestoreReplaysSecrets:
    """restore_k8s.yml must apply the snapshot's secrets-<id>.yaml
    file when present, so a fresh-cluster restore brings back
    MYSQL_PASSWORD / MW_SECRET_KEY (#510)."""

    RESTORE_K8S = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml",
    )

    def test_restore_apply_secrets_task_exists(self):
        with open(self.RESTORE_K8S) as f:
            content = f.read()
        # The task name explicitly mentions Secrets; the command pipes
        # through 'kubectl apply -f -'. Both should be present.
        assert "Restore canasta-managed K8s Secrets" in content, (
            "restore_k8s.yml must have a task that restores the "
            "canasta-managed Secrets from the snapshot (#510)"
        )
        assert "kubectl apply" in content, (
            "restore_k8s.yml's secrets-restore task must use "
            "'kubectl apply' so it's idempotent against an existing "
            "Secret with the same value"
        )

    def test_restore_skips_when_secrets_file_missing(self):
        """Snapshots taken before #510 won't have a secrets-*.yaml
        file. Restoring those must skip the secrets task gracefully,
        not fail."""
        with open(self.RESTORE_K8S) as f:
            content = f.read()
        # The when-guard checks for the filename being non-empty
        assert "_restore_secrets_filename" in content, (
            "restore_k8s.yml should resolve the secrets filename "
            "conditionally so pre-#510 snapshots restore cleanly"
        )
        assert "_restore_secrets_filename != ''" in content, (
            "restore_k8s.yml's secrets-restore task must guard "
            "on the filename being non-empty (pre-#510 snapshots)"
        )
