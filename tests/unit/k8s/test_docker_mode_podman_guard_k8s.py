"""The docker-mode Podman guard leaves Kubernetes instances alone.

A Kubernetes record is not driven through podman-compose, so the guard
does not refuse it even when its socket fields name Podman.
"""

import canasta
import test_docker_mode_podman_guard
from test_docker_mode_podman_guard import PODMAN_LOCAL, _args

registry = test_docker_mode_podman_guard.registry


class TestItDoesNotRefuseAnythingElse:
    def _ok(self, args):
        assert canasta.check_docker_mode_can_reach_runtime(args) is None

    def test_a_kubernetes_instance_is_not_compose_and_is_allowed(self,
                                                                 registry):
        k8s = dict(PODMAN_LOCAL, id="k1", orchestrator="kubernetes")
        registry({"k1": k8s})
        self._ok(_args(id="k1"))
