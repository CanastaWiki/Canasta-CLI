"""`canasta stop` on the Ansible path for Kubernetes records the instance as
running when any of its deployments has a ready replica, or when the replica
count cannot be read."""

import pytest

from test_stop_already_stopped import ORCH_STOP, _render, _task


class TestAnsiblePath:

    def _was_running(self, k8s, rc=0, stdout=""):
        task = _task(ORCH_STOP, "Record whether the instance was running")
        result = {"rc": rc, "stdout": stdout}
        return _render(task["ansible.builtin.set_fact"]["_stop_was_running"],
                       instance_is_k8s=k8s,
                       _stop_k8s_replicas=result if k8s else {},
                       _stop_compose_ps={} if k8s else result)

    @pytest.mark.parametrize("k8s,rc,stdout,expected", [
        (True, 0, "", "False"),
        (True, 0, "0 0 0", "False"),
        (True, 0, "0 1 0", "True"),
        (True, 1, "", "True"),
    ])
    def test_was_running(self, k8s, rc, stdout, expected):
        assert self._was_running(k8s, rc, stdout) == expected
