"""The Kubernetes restore marks its files phase before its first change.
"""

import os

from test_restore_snapshot_checks import ORCH, _names


RESTORE_K8S = os.path.join(ORCH, "k8s_restore_instance.yml")


class TestFilesPhaseIsMarkedBeforeTheFirstChange:
    def test_kubernetes_marks_before_the_restore_pod(self):
        names = _names(RESTORE_K8S)
        assert names.index("Mark the files phase") < names.index("Create restore pod")
