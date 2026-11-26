"""Doctor's reclaimable-space line on a k3s cluster host notes that the
Docker figure leaves containerd images unmeasured and still names the
reclaim command, while a host whose kubeconfig only points at another
cluster gets no such caveat."""

from test_doctor_reclaimable import DF_WITH_VOLUMES, NO_K3S, _reclaimable_line


# The k3s probe, which is what tells a cluster host from a host whose
# kubeconfig merely points at someone else's cluster.
K3S = "k3s version v1.31.5+k3s1"


def test_kubeconfig_pointing_elsewhere_is_not_a_cluster_host():
    """A laptop whose kubectl reaches a remote cluster stores no
    containerd images of its own, so the caveat would be noise."""
    line = _reclaimable_line(DF_WITH_VOLUMES, NO_K3S)
    assert "containerd" not in line


def test_cluster_host_without_docker_still_names_the_reclaim_command():
    line = _reclaimable_line("unknown", K3S)
    assert "canasta image prune" in line
    assert "containerd" in line


def test_cluster_host_labels_the_docker_figure_as_partial():
    line = _reclaimable_line(DF_WITH_VOLUMES, K3S)
    assert "not measured" in line
    assert "5.1GB images" in line
