"""image prune keeps the host's canasta-base:local while a Kubernetes
instance is on the host."""

from test_image_reclaim_canasta_base import _removable


def test_base_is_kept_on_a_host_with_a_kubernetes_instance():
    assert _removable(["canasta-base:local"], k8s=["k"]) == []
