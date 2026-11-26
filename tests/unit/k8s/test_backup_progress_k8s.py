"""The Kubernetes backup Job makes restic print progress with no terminal
attached, so `kubectl logs -f` shows a long backup working."""

import os

from test_backup_progress import REPO_ROOT


K8S_RUN_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")


class TestResticProgress:
    def test_kubernetes_job_sets_progress_fps(self):
        with open(K8S_RUN_BACKUP) as f:
            content = f.read()
        assert "RESTIC_PROGRESS_FPS" in content, (
            "a Job's container has no terminal either, so `kubectl logs -f` "
            "shows nothing while a long backup runs"
        )
