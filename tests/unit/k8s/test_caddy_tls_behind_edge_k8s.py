"""doctor's Caddy tls-directive check says nothing for a Kubernetes
instance, whose Caddy is not behind the HAProxy edge."""

from test_caddy_tls_behind_edge import _instance, doctor


def test_doctor_is_quiet_on_kubernetes(tmp_path):
    path = _instance(tmp_path, site="tls internal\n")
    assert doctor._caddy_tls_lines({"id": "one", "path": path,
                                    "orchestrator": "k8s"}) == []
