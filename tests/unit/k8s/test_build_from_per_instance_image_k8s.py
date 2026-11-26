"""Kubernetes build-from instances keep the shared canasta:local tag.

values.yaml and the in-cluster registry push refer to canasta:local, so a
Kubernetes build names its output that, and upgrade does not move a
Kubernetes instance onto a per-instance tag.
"""

from test_build_from_per_instance_image import (
    BUILD_FROM,
    IMAGE_TAG,
    _render,
    _task,
    _when,
)


class TestBuildName:
    def _name(self, **variables):
        fact = _task(BUILD_FROM, "Name the image this build produces")[
            "ansible.builtin.set_fact"]["_build_image_name"]
        return str(_render(fact, **variables)).strip()

    def test_kubernetes_keeps_the_shared_name(self):
        assert self._name(instance_is_k8s=True, id="wiki") == "canasta:local"


class TestUpgradeMovesOffTheSharedTag:
    BLOCK = "Point a rebuilt Compose instance at its own image tag"

    def _applies(self, image="canasta:local", k8s=False, rebuilt=True,
                 built="canasta:local-wiki"):
        return _when(
            _task(IMAGE_TAG, self.BLOCK),
            instance_is_k8s=k8s,
            _upgrade_rebuild=rebuilt,
            _built_image=built,
            _mig={"env": {"CANASTA_IMAGE": image},
                  "env_present": {"CANASTA_IMAGE": image != ""}})

    def test_kubernetes_is_left_alone(self):
        assert not self._applies(k8s=True, built="canasta:local")
