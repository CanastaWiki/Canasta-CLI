"""The create preflight's k3s gate covers only the ports Traefik claims.

A running k3s blocks a Compose create only when the instance binds port 80
or 443.
"""

from test_port_preflight_portable import _tasks


class TestPortsComeFromTheEnvFile:
    def _named(self, needle):
        return [
            t for t in _tasks()
            if isinstance(t.get("name"), str) and needle in t["name"]
        ]

    def test_k3s_gate_only_fires_for_the_traefik_ports(self):
        tasks = self._named("Fail if k3s is running")
        assert tasks, "k3s gate disappeared"
        when = tasks[0].get("when")
        when_text = " ".join(when) if isinstance(when, list) else str(when)
        assert "_preflight_http_port" in when_text, (
            "Traefik claims 80 and 443 only; an instance binding neither "
            "must not be blocked by a running k3s")
