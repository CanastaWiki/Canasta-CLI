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
    """The backup Pod's ServiceAccount must have a Role + RoleBinding
    that grants read on the canasta-managed Secrets. Scoped narrowly:
    only the two Secret names we dump, only `get` verb, namespace-
    local. Least-privilege RBAC.

    Source of truth is roles/backup/tasks/_k8s_backup_rbac.yml — both
    the CronJob path (schedule_set.yml) and the on-demand path
    (k8s_run_backup.yml) include this file (#513 refactor)."""

    SHARED_RBAC_PATH = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "_k8s_backup_rbac.yml",
    )

    def _find_resources(self, kind):
        with open(self.SHARED_RBAC_PATH) as f:
            tasks = yaml.safe_load(f)
        for task in _walk_tasks(tasks):
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


class TestK8sRestorePreservesCurrentDBPassword:
    """When the snapshot's secrets manifest is applied, the running
    cluster's MYSQL_PASSWORD / MYSQL_ROOT_PASSWORD must be preserved
    over the snapshot's values — same policy Compose's restore.yml
    follows ('Save current DB password before restore' + 'Preserve
    DB password in restored .env'). Without this, a clone-into-fresh-
    cluster restore auth-mismatches: the running DB pod's mysql.user
    table has hashes of the cluster's CURRENT password, not the
    snapshot's."""

    RESTORE_K8S = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml",
    )

    def _content(self):
        with open(self.RESTORE_K8S) as f:
            return f.read()

    def test_saves_current_db_secret_before_apply(self):
        content = self._content()
        # Two anchors: the k8s_info read of <id>-db-credentials, and
        # the set_fact that captures the current MYSQL_PASSWORD.
        assert "k8s_info:" in content, (
            "restore_k8s.yml must read the cluster's current "
            "<id>-db-credentials Secret BEFORE applying the snapshot's "
            "Secrets, so the live DB user's password can be preserved."
        )
        assert "_restore_current_mysql_pwd" in content, (
            "restore_k8s.yml must capture the running cluster's "
            "MYSQL_PASSWORD into a fact for re-application post-restore"
        )
        assert "_restore_current_mysql_root_pwd" in content, (
            "restore_k8s.yml must also capture MYSQL_ROOT_PASSWORD; "
            "both DB user passwords were hashed against the running "
            "cluster's values during pod init"
        )

    def test_patches_db_secret_back_after_apply(self):
        content = self._content()
        # The strategic-merge patch on <id>-db-credentials with both
        # password keys, gated on the snapshot's secrets file existing.
        assert "state: patched" in content, (
            "restore_k8s.yml must use 'state: patched' (strategic merge) "
            "to re-apply MYSQL_PASSWORD without touching MW_SECRET_KEY "
            "in the snapshot-restored Secret"
        )
        assert "Preserve current DB password in restored Secret" in content, (
            "restore_k8s.yml is missing the explicit preserve task — "
            "without it, fresh-cluster restore auth-mismatches"
        )

    def test_preserve_step_runs_after_secret_apply(self):
        """Order matters: snapshot apply MUST come before the patch.
        If the order were inverted, the patch would land first and
        then the snapshot's apply would overwrite it back, defeating
        the preservation."""
        content = self._content()
        save_pos = content.find("Save current DB credentials before secret restore")
        apply_pos = content.find("Restore canasta-managed K8s Secrets from snapshot")
        patch_pos = content.find("Preserve current DB password in restored Secret")
        assert save_pos != -1 and apply_pos != -1 and patch_pos != -1, (
            "Three-step preservation flow expected: save current → "
            "apply snapshot → patch back. One or more tasks missing."
        )
        assert save_pos < apply_pos < patch_pos, (
            "Order must be: save current creds, then apply snapshot's "
            "Secrets, then patch the cluster's creds back. Got "
            "save=%d apply=%d patch=%d." % (save_pos, apply_pos, patch_pos)
        )

    def test_preserve_uses_stringdata_not_data(self):
        """stringData lets the K8s API server do the b64 conversion;
        attempting raw data with un-encoded values would fail."""
        content = self._content()
        # Find the Preserve task block and assert stringData appears
        # after it. (Cheap proximity check — sufficient for a
        # structural guard.)
        patch_pos = content.find("Preserve current DB password in restored Secret")
        block = content[patch_pos:patch_pos + 800]
        assert "stringData:" in block, (
            "Preserve task must use 'stringData:' so the K8s API "
            "server handles the base64 conversion of MYSQL_PASSWORD"
        )


class TestOnDemandBackupCapturesDB:
    """Guard #513: on-demand 'canasta backup create' on K8s must
    capture the wiki database. Pre-fix the dump ran via 'kubectl
    exec' into the live web pod's emptyDir; the restic Job spawned
    separately and mounted the web-config ConfigMap (different
    volume), so the SQL dumps silently vanished from the snapshot.
    """

    K8S_RUN_BACKUP = os.path.join(
        REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml",
    )
    CREATE_YML = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "create.yml",
    )

    @staticmethod
    def _walk(tasks):
        for t in tasks or []:
            if not isinstance(t, dict):
                continue
            yield t
            for nested in ("block", "rescue", "always"):
                if nested in t:
                    yield from TestOnDemandBackupCapturesDB._walk(t[nested])

    def _load_run_backup(self):
        with open(self.K8S_RUN_BACKUP) as f:
            return yaml.safe_load(f)

    def test_detects_backup_operation(self):
        """The on-demand path must distinguish 'backup' from
        'forget'/'snapshots'/'init'/etc., so init-container overhead
        only runs when actually needed."""
        for task in self._walk(self._load_run_backup()):
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if isinstance(sf, dict) and "_is_backup_op" in sf:
                expr = sf["_is_backup_op"]
                assert "'backup' in backup_args" in expr, (
                    "_is_backup_op should test for 'backup' in backup_args"
                )
                return
        raise AssertionError(
            "k8s_run_backup.yml must define _is_backup_op so the DB- "
            "and Secret-dump init containers only fire on backup ops "
            "(#513)"
        )

    def test_init_containers_built_for_backup_ops(self):
        """A _backup_init_containers fact must be defined and
        populated only on backup ops."""
        # Default-empty case
        found_default_empty = False
        # Backup-op-conditional case
        found_backup_op_conditional = False
        for task in self._walk(self._load_run_backup()):
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if not (isinstance(sf, dict) and "_backup_init_containers" in sf):
                continue
            value = sf["_backup_init_containers"]
            when = task.get("when", "")
            if value == [] or value == "[]":
                found_default_empty = True
            elif "_is_backup_op" in str(when):
                # Conditional set_fact for backup ops — value should
                # be a list of two init containers.
                if isinstance(value, list) and len(value) == 2:
                    names = [c.get("name") for c in value]
                    assert "dump-databases" in names, (
                        "Backup-op init containers must include "
                        "dump-databases"
                    )
                    assert "dump-secrets" in names, (
                        "Backup-op init containers must include "
                        "dump-secrets (matches scheduled CronJob, "
                        "consolidates with #510)"
                    )
                    found_backup_op_conditional = True
        assert found_default_empty, (
            "k8s_run_backup.yml must default _backup_init_containers "
            "to [] so non-backup ops (forget, snapshots, etc.) skip "
            "the dump overhead"
        )
        assert found_backup_op_conditional, (
            "k8s_run_backup.yml must conditionally populate "
            "_backup_init_containers with both dump-databases and "
            "dump-secrets when _is_backup_op (#513 + #510 symmetry)"
        )

    def test_dumps_volume_added_for_backup_ops(self):
        """The dumps emptyDir + mount-on-mount under
        /currentsnapshot/config/backup must exist when backup-op,
        matching the schedule_set.yml CronJob's pattern."""
        with open(self.K8S_RUN_BACKUP) as f:
            content = f.read()
        # Both the volume and the mount point must be added together
        # in a backup-op-gated set_fact.
        assert "'name': 'dumps'" in content, (
            "k8s_run_backup.yml must add a 'dumps' emptyDir volume "
            "for backup ops"
        )
        assert "/currentsnapshot/config/backup" in content, (
            "k8s_run_backup.yml must mount the dumps emptyDir at "
            "/currentsnapshot/config/backup (mount-on-mount over "
            "the web-config ConfigMap subdir)"
        )

    def test_rbac_included_for_backup_ops(self):
        """k8s_run_backup.yml must include the shared _k8s_backup_rbac
        task when running a backup op so the SA/Role/RoleBinding for
        dump-secrets exists. The same shared file is included by
        schedule_set.yml — single source of truth."""
        with open(self.K8S_RUN_BACKUP) as f:
            content = f.read()
        assert "_k8s_backup_rbac.yml" in content, (
            "k8s_run_backup.yml must include the shared "
            "_k8s_backup_rbac.yml task file (factored out per #513)"
        )

    def test_create_dump_gated_on_compose(self):
        """create.yml's mariadb-dump-via-kubectl-exec is a no-op on
        K8s (the dump lands in the live web pod's emptyDir, invisible
        to the restic Job). Must be gated on Compose so it doesn't
        run uselessly on K8s."""
        with open(self.CREATE_YML) as f:
            tasks = yaml.safe_load(f)
        for task in self._walk(tasks):
            name = task.get("name", "")
            if "Dump each wiki database" not in name:
                continue
            when = task.get("when", "")
            assert "compose" in str(when).lower(), (
                "create.yml's 'Dump each wiki database' task must be "
                "gated on the Compose orchestrator — running on K8s "
                "writes to a transient emptyDir the restic Job can't "
                "see (#513)"
            )
            return
        raise AssertionError(
            "create.yml has no 'Dump each wiki database' task"
        )


class TestSharedBackupRBAC:
    """The SA + Role + RoleBinding live in roles/backup/tasks/
    _k8s_backup_rbac.yml so the CronJob (schedule_set.yml) and the
    on-demand Job (k8s_run_backup.yml) stay in lockstep. Schedule_set
    must include the shared file (refactored per #513)."""

    SHARED_RBAC = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "_k8s_backup_rbac.yml",
    )

    def test_shared_file_exists(self):
        assert os.path.isfile(self.SHARED_RBAC), (
            "Expected shared RBAC file at roles/backup/tasks/"
            "_k8s_backup_rbac.yml (#513)"
        )

    def test_shared_file_creates_three_resources(self):
        """ServiceAccount, Role, RoleBinding."""
        with open(self.SHARED_RBAC) as f:
            tasks = yaml.safe_load(f)
        kinds = []
        for task in tasks:
            k8s = task.get("kubernetes.core.k8s") or task.get("k8s") or {}
            defn = k8s.get("definition") or {}
            if defn.get("kind"):
                kinds.append(defn["kind"])
        assert kinds == ["ServiceAccount", "Role", "RoleBinding"], (
            "Shared RBAC file must create exactly: ServiceAccount, "
            "Role, RoleBinding (got %r)" % kinds
        )

    def test_schedule_set_uses_shared_include(self):
        """schedule_set.yml must include the shared file rather than
        defining its own copy — single source of truth, no drift."""
        with open(SCHEDULE_SET) as f:
            content = f.read()
        assert "_k8s_backup_rbac.yml" in content, (
            "schedule_set.yml must include the shared "
            "_k8s_backup_rbac.yml task file (refactored per #513 to "
            "share with k8s_run_backup.yml)"
        )


class TestK8sRestoreImportRegexNotDoubleEscaped:
    """The restore loop's regex `'^db_.*\.sql$'` and the secrets-
    filename regex `'^secrets-.+\.yaml$'` must use a SINGLE backslash
    in the YAML block scalar context. With `\\.` (two), Ansible's
    Jinja parses the literal `\\.` regex which is "literal backslash
    + any char" — never matches `.sql` / `.yaml`. The Import + all
    secrets-restore tasks were silently skipping for every K8s
    restore. Surfaced during PR #518 validation."""

    RESTORE_K8S = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml",
    )

    def _content(self):
        with open(self.RESTORE_K8S) as f:
            return f.read()

    def test_db_dump_regex_uses_single_backslash(self):
        c = self._content()
        assert "'^db_.*\\.sql$'" in c, (
            "restore_k8s.yml must filter dumps with `'^db_.*\\.sql$'`"
        )
        assert "'^db_.*\\\\.sql$'" not in c, (
            "restore_k8s.yml has `\\\\.sql` (double-escaped) in the "
            "dump-import regex; in YAML block scalar this becomes the "
            "regex `\\.` for 'literal backslash + any char', never "
            "matches *.sql, and the import task silently skips."
        )

    def test_secrets_filename_regex_uses_single_backslash(self):
        c = self._content()
        assert "'^secrets-.+\\.yaml$'" in c
        assert "'^secrets-.+\\\\.yaml$'" not in c, (
            "Same regex bug for the secrets-filename match — would "
            "silently skip every secrets-restore task"
        )


class TestK8sRestoreStalePodCleanup:
    """If a previous restore failed mid-run, the restic-restore pod
    persists. kubernetes.core.k8s state: present then can't update
    it (Pod spec is immutable post-creation), and the restore aborts.
    Need to delete first."""

    RESTORE_K8S = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml",
    )

    def test_stale_pod_cleanup_before_create(self):
        with open(self.RESTORE_K8S) as f:
            tasks = yaml.safe_load(f)
        clean_idx = create_idx = -1
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                continue
            name = t.get("name", "")
            if name.startswith("Clean up any stale restic-restore"):
                clean_idx = i
                # Must be state: absent on the pod
                k8s = t.get("kubernetes.core.k8s") or t.get("k8s") or {}
                assert k8s.get("state") == "absent", (
                    "Stale-pod cleanup must use state: absent"
                )
            elif name == "Create restore pod":
                create_idx = i
        assert clean_idx != -1, (
            "restore_k8s.yml must have a stale-pod cleanup task before "
            "Create restore pod (kubernetes.core.k8s state: present "
            "can't update an existing Pod)"
        )
        assert create_idx != -1
        assert clean_idx < create_idx, (
            "Cleanup must run before Create restore pod, not after"
        )


class TestK8sRestoreNoComposeRenderForK8sGitops:
    """K8s gitops uses values.template.yaml + hosts/<name>/vars.yaml,
    not env.template. restore_k8s.yml previously invoked
    render_compose.yml which crashes with 'File not found:
    env.template' for K8s gitops instances. The include must be
    removed or replaced with K8s-aware logic."""

    RESTORE_K8S = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml",
    )

    def test_does_not_invoke_render_compose(self):
        """Strip comment lines before checking — explanatory comments
        may legitimately mention the old anti-pattern."""
        with open(self.RESTORE_K8S) as f:
            non_comment = "\n".join(
                line for line in f.read().splitlines()
                if not line.lstrip().startswith("#")
            )
        assert "render_compose.yml" not in non_comment, (
            "restore_k8s.yml must not invoke render_compose.yml — that "
            "task expects env.template (Compose-only artifact) and "
            "fails for K8s gitops instances. See #521."
        )


class TestDiffComposeMatchRegex:
    """canasta gitops diff's display message uses regexes for filename
    matching. Same `\\.` block-scalar bug as restore_k8s.yml — the
    regex never matches anything, so 'A restart would be needed' /
    'A maintenance update may be needed' messages would never trigger
    even when there are matching .env / .yml / .php changes."""

    DIFF_COMPOSE = os.path.join(
        REPO_ROOT, "roles", "gitops", "tasks", "diff_compose.yml",
    )

    def test_regex_uses_single_backslash(self):
        with open(self.DIFF_COMPOSE) as f:
            content = f.read()
        # Should have single backslash for literal dot
        assert "'.*\\.(env|yml|yaml)$'" in content
        assert "'.*\\.php$'" in content
        # Must not have the broken double-backslash form
        assert "'.*\\\\.(env|yml|yaml)$'" not in content, (
            "diff_compose.yml has `\\\\.` in regex; would never match"
        )
        assert "'.*\\\\.php$'" not in content, (
            "diff_compose.yml has `\\\\.php` in regex; would never match"
        )


class TestDumpSecretsStripsEphemeralMetadata:
    """`kubectl get secret -o yaml` emits resourceVersion, uid, and
    creationTimestamp. `kubectl apply -f` then refuses to apply,
    erroring with 'object has been modified'. Both dump-secrets init
    containers (scheduled CronJob + on-demand Job) must strip those
    fields with sed."""

    SCHEDULE_SET = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "schedule_set.yml",
    )
    K8S_RUN_BACKUP = os.path.join(
        REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml",
    )

    def _has_strip_in_dump_secrets(self, path):
        with open(path) as f:
            content = f.read()
        # The dump-secrets command must include the sed strip
        # and target the three ephemeral fields. Check both
        # at-once.
        return all(
            field in content
            for field in (
                "dump-secrets",
                "resourceVersion",
                "uid",
                "creationTimestamp",
                "sed",
            )
        )

    def test_schedule_set_strips_metadata(self):
        assert self._has_strip_in_dump_secrets(self.SCHEDULE_SET), (
            "schedule_set.yml dump-secrets must sed-strip "
            "resourceVersion / uid / creationTimestamp; without it "
            "kubectl apply at restore time fails with 'object has "
            "been modified' resourceVersion conflict"
        )

    def test_k8s_run_backup_strips_metadata(self):
        assert self._has_strip_in_dump_secrets(self.K8S_RUN_BACKUP), (
            "k8s_run_backup.yml dump-secrets must sed-strip "
            "resourceVersion / uid / creationTimestamp; same "
            "rationale as schedule_set.yml"
        )


class TestK8sRestoreCloudBackendDoesNotMountHostPath:
    """Guard #519: cloud-backend RESTIC_REPOSITORY values (s3:...,
    b2:..., azure:..., gs:...) are URIs, not filesystem paths.
    Pre-fix, restore_k8s.yml unconditionally mounted RESTIC_REPOSITORY
    as a hostPath; for cloud URIs this failed container init with
    'no such file or directory' and the entire restore aborted.
    Only local-path repos (^/...) should get the hostPath bind.
    Mirrors the same detection schedule_set.yml and k8s_run_backup.yml
    use for the backup direction."""

    RESTORE_K8S = os.path.join(
        REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml",
    )

    def _content(self):
        with open(self.RESTORE_K8S) as f:
            return f.read()

    def test_local_repo_detection_present(self):
        """A `_restore_local_repo` fact must be set from a regex
        check of the RESTIC_REPOSITORY against `^/`. That mirrors the
        backup-side detection in schedule_set.yml and k8s_run_backup.yml."""
        c = self._content()
        assert "_restore_local_repo:" in c, (
            "restore_k8s.yml must define _restore_local_repo to "
            "distinguish path-style RESTIC_REPOSITORY from URIs (#519)"
        )
        assert "is match('^/')" in c, (
            "restore_local_repo detection must use the same regex as "
            "the backup paths: RESTIC_REPOSITORY is match('^/')"
        )

    def test_volumes_built_conditionally(self):
        """The local-repo volume + mount must be added via a
        conditional set_fact, NOT inlined unconditionally in the
        Pod definition."""
        c = self._content()
        # The conditional add tasks
        assert "Add local-repo mount when RESTIC_REPOSITORY is a path" in c, (
            "restore_k8s.yml needs the conditional volume-mount append"
        )
        assert "Add local-repo hostPath volume when RESTIC_REPOSITORY is a path" in c, (
            "restore_k8s.yml needs the conditional volume append"
        )
        # And both gated on _restore_local_repo
        # Each conditional add should have when: _restore_local_repo
        for task_marker in (
            "Add local-repo mount when RESTIC_REPOSITORY is a path",
            "Add local-repo hostPath volume when RESTIC_REPOSITORY is a path",
        ):
            idx = c.find(task_marker)
            block = c[idx:idx + 600]
            assert "when: _restore_local_repo" in block, (
                "%r must be gated on _restore_local_repo" % task_marker
            )

    def test_pod_uses_built_volumes_facts(self):
        """The Create restore pod task should reference
        `_restore_volume_mounts` and `_restore_volumes`, not inline
        volumes/volumeMounts. Inline values would re-introduce the
        unconditional hostPath mount that #519 was about."""
        c = self._content()
        # The Create restore pod block should reference the facts
        idx = c.find("Create restore pod")
        block = c[idx:idx + 1500]
        assert "_restore_volume_mounts" in block, (
            "Create restore pod must read volumeMounts from "
            "_restore_volume_mounts fact (built conditionally)"
        )
        assert "_restore_volumes" in block, (
            "Create restore pod must read volumes from "
            "_restore_volumes fact (built conditionally)"
        )

    def test_no_unconditional_local_repo_volume(self):
        """The old shape — inline `local-repo` volume with a hardcoded
        hostPath of `RESTIC_REPOSITORY` — must NOT be present. That
        was the #519 bug."""
        c = self._content()
        # Find the Create restore pod block and assert no inline
        # local-repo volume in it.
        idx = c.find("- name: Create restore pod")
        end = c.find("- name: Wait for restore pod", idx)
        block = c[idx:end] if end != -1 else c[idx:idx + 2000]
        # The literal pattern that was wrong: hostPath: path: "{{ ... }}"
        # under a volumes: stanza in the Pod definition.
        assert "type: DirectoryOrCreate" not in block, (
            "Create restore pod should not contain an inline "
            "hostPath/DirectoryOrCreate — that was the #519 bug "
            "(cloud backends fail container init). The conditional "
            "_restore_volumes append handles local-path repos only."
        )
