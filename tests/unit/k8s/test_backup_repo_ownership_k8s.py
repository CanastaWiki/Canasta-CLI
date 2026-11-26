"""The Kubernetes backup Job reclaims a local-path repo's ownership.

k8s_run_backup.yml chowns the restic repository back after the run, as the
Compose path does, so a repo on a host path stays manageable by the
operator.
"""

import os

from test_backup_repo_ownership import REPO_ROOT


K8S_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")


class TestBothOrchestratorsAgree:
    def test_k8s_still_reclaims_too(self):
        # If the K8s side ever loses this, the asymmetry returns the other
        # way round and this test says so.
        with open(K8S_BACKUP) as f:
            body = f.read()
        assert "chown -R" in body, (
            "k8s_run_backup.yml no longer reclaims repo ownership; the two "
            "orchestrators have diverged again"
        )
