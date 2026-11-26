"""The upgrade removes the in-cluster backup CronJob older Kubernetes
instances were scheduled by, now that the crontab schedule replaces it.

The migration deletes only that CronJob, only on Kubernetes, and runs on
upgrade.
"""

import os

from test_upgrade_legacy_cleanup_migrations import MIGRATIONS, UPGRADE_MAIN, _load

CRONJOB_MIGRATION = os.path.join(MIGRATIONS,
                                 "remove_legacy_backup_cronjob.yml")


class TestCronJobMigration:
    def test_targets_the_legacy_cronjob_on_kubernetes_only(self):
        task = _load(CRONJOB_MIGRATION)[0]
        spec = task["kubernetes.core.k8s"]
        assert spec["state"] == "absent"
        assert spec["kind"] == "CronJob"
        assert spec["name"] == "canasta-backup-{{ instance_id }}"
        assert spec["namespace"] == "canasta-{{ instance_id }}"
        assert task["when"] == "instance_is_k8s | bool"
        # An absent CronJob is not a failure, so any failure is real.
        assert "ignore_errors" not in task


class TestWiring:
    def test_cronjob_migration_runs_on_upgrade(self):
        loop = next(t for t in _load(UPGRADE_MAIN)
                    if t.get("name") == "Run migrations")["loop"]
        assert "remove_legacy_backup_cronjob.yml" in loop
