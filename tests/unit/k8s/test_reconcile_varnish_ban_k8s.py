"""reconcile decides whether Varnish is in use on Kubernetes from values.yaml."""

from test_reconcile_varnish_ban import _enabled


class TestVarnishInUse:
    def test_kubernetes_reads_values(self):
        assert _enabled("kubernetes", values="varnish:\n  enabled: true\n")
        assert not _enabled("kubernetes",
                            values="varnish:\n  enabled: false\n")

    def test_kubernetes_without_values_is_off(self):
        assert not _enabled("kubernetes", values="")
