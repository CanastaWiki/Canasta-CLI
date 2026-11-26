"""`canasta doctor` on Kubernetes treats Traefik's default certificate as
self-signed, pointing at a cert-manager Certificate, unless the Helm values
show the instance was created with --skip-tls and no cert-manager, where no
certificate is by design."""

from test_doctor_origin_tls_self_signed import TRAEFIK, _probe, _report, doctor


SKIP_TLS_VALUES = "domains:\n- wiki.example.org\nsecrets:\n  dbSecretName: x\n"
CERT_MANAGER_VALUES = (
    "domains:\n- wiki.example.org\ningress:\n  tls: true\n"
    "  certManager:\n    enabled: true\n    issuer: letsencrypt-prod\n")


class TestSelfSigned:
    def test_traefik_default_certificate_warns_on_k8s(self):
        out = _report(_probe(TRAEFIK, TRAEFIK), k8s=True)
        assert "WARN — self-signed certificate (issuer %s)" % TRAEFIK in out
        assert "cert-manager Certificate" in out
        assert "canasta-site" in out
        assert "OK" not in out

    def test_traefik_default_matched_by_name_without_a_subject(self):
        out = _report(_probe(TRAEFIK, ""), k8s=True)
        assert "WARN — self-signed certificate" in out


class TestSkipTls:
    def test_probe_reads_values_yaml(self):
        assert "VALUES:" in doctor._ORIGIN_TLS_SCRIPT
        assert "%(values)s" in doctor._ORIGIN_TLS_SCRIPT

    def test_parse_cert_manager(self):
        assert doctor._parse_cert_manager(
            _probe(TRAEFIK, TRAEFIK, values=SKIP_TLS_VALUES)) is False
        assert doctor._parse_cert_manager(
            _probe(TRAEFIK, TRAEFIK, values=CERT_MANAGER_VALUES)) is True
        assert doctor._parse_cert_manager(_probe(TRAEFIK, TRAEFIK)) is None

    def test_traefik_default_is_informational_without_cert_manager(self):
        out = _report(
            _probe(TRAEFIK, TRAEFIK, values=SKIP_TLS_VALUES), k8s=True)
        assert ("wiki.example.org: no TLS certificate by design" in out)
        assert "--skip-tls" in out
        assert "WARN" not in out
        assert "cert-manager Certificate" not in out

    def test_traefik_default_warns_with_cert_manager(self):
        out = _report(
            _probe(TRAEFIK, TRAEFIK, values=CERT_MANAGER_VALUES), k8s=True)
        assert "WARN — self-signed certificate" in out
        assert "cert-manager Certificate" in out
        assert "by design" not in out

    def test_unreadable_values_still_warns(self):
        out = _report(_probe(TRAEFIK, TRAEFIK, values=": :\n  - ["), k8s=True)
        assert "WARN — self-signed certificate" in out
