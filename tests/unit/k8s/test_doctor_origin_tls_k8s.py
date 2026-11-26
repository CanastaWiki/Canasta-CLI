"""`canasta doctor` probes a Kubernetes instance's origin certificate on the ingress.

The probe connects to port 443, where the ingress controller listens,
rather than to the instance's configured HTTPS port.
"""

from test_doctor_origin_tls import doctor


class TestOriginTlsGate:
    def _inst(self, **kw):
        inst = {"id": "site", "orchestrator": "compose", "path": "/srv/site",
                "host": "localhost"}
        inst.update(kw)
        return inst

    def test_kubernetes_probes_the_ingress_on_443(self, monkeypatch):
        seen = {}

        class _R:
            stdout = ""

        monkeypatch.setattr(
            doctor._helpers, "_read_wikis",
            lambda path, host: [{"url": "a.example.org"}])
        monkeypatch.setattr(doctor._helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(
            doctor.subprocess, "run",
            lambda *a, **k: (seen.update(script=a[0][2]) or _R()))
        doctor._origin_tls_lines(self._inst(orchestrator="kubernetes"))
        assert "127.0.0.1:'443'" in seen["script"]
