"""create keeps an https:// MW_SITE_SERVER on Kubernetes, where the ingress
terminates TLS, by not deriving the HTTP-only fact from the orchestrator."""

from test_create_site_server_scheme import _http_only_expr


class TestSkipTlsScheme:
    def test_http_only_is_not_forced_by_orchestrator(self):
        """K8s must keep https://, or MediaWiki emits http:// links.

        On Kubernetes the ingress terminates TLS, so https:// is the
        correct $wgServer.
        """
        expr = _http_only_expr()
        for token in ("orchestrator", "kubernetes", "k8s"):
            assert token not in expr, (
                "_http_only must not key off the orchestrator: on K8s the "
                "ingress terminates TLS and MW_SITE_SERVER stays https"
            )
