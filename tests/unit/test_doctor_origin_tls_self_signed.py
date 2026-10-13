"""`canasta doctor` flags self-signed origin certificates."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from direct_commands import doctor  # noqa: E402


NOW = datetime.datetime(2026, 7, 31, tzinfo=datetime.timezone.utc)
LATER = (NOW + datetime.timedelta(days=60)).strftime("%b %e %H:%M:%S %Y GMT")
D = doctor._helpers._SENTINEL

LE = "C=US, O=Let's Encrypt, CN=R11"
STAGING = "C=US, O=(STAGING) Let's Encrypt, CN=(STAGING) Artificial Amaranth YE1"
CADDY_LOCAL = "CN=Caddy Local Authority - ECC Intermediate"
TRAEFIK = "CN=TRAEFIK DEFAULT CERT"
SELF = "CN=wiki.example.org"


SKIP_TLS_VALUES = "domains:\n- wiki.example.org\nsecrets:\n  dbSecretName: x\n"
CERT_MANAGER_VALUES = (
    "domains:\n- wiki.example.org\ningress:\n  tls: true\n"
    "  certManager:\n    enabled: true\n    issuer: letsencrypt-prod\n")


def _probe(issuer, subject, name="wiki.example.org", tls_internal="",
           values=None):
    values_seg = "" if values is None else "%sVALUES:\n%s" % (D, values)
    return (
        "%sTLSINTERNAL:%s\n%s"
        "%sNAME:%s\nsubject=%s\nissuer=%s\nnotAfter=%s\n"
        % (D, tls_internal, values_seg, D, name, subject, issuer, LATER))


def _report(stdout, k8s=False):
    return "\n".join(doctor._origin_tls_lines_from_entries(
        "site", doctor._parse_origin_tls(stdout), NOW,
        tls_internal=doctor._parse_tls_internal(stdout), k8s=k8s,
        cert_manager=doctor._parse_cert_manager(stdout) if k8s else None))


class TestProbe:
    def test_probe_reads_the_subject(self):
        assert "-subject" in doctor._ORIGIN_TLS_SCRIPT

    def test_parser_returns_the_subject(self):
        assert doctor._parse_origin_tls(_probe(SELF, SELF)) == [
            ("wiki.example.org", SELF, LATER, SELF)]


class TestSelfSigned:
    def test_self_signed_certificate_warns(self):
        out = _report(_probe(SELF, SELF))
        assert "WARN — self-signed certificate" in out
        assert "OK" not in out
        assert "Caddyfile.site" in out

    def test_traefik_default_certificate_warns_on_k8s(self):
        out = _report(_probe(TRAEFIK, TRAEFIK), k8s=True)
        assert "WARN — self-signed certificate (issuer %s)" % TRAEFIK in out
        assert "cert-manager Certificate" in out
        assert "canasta-site" in out
        assert "OK" not in out

    def test_traefik_default_matched_by_name_without_a_subject(self):
        out = _report(_probe(TRAEFIK, ""), k8s=True)
        assert "WARN — self-signed certificate" in out

    def test_self_signed_on_a_local_name_is_exempt(self):
        out = _report(_probe(TRAEFIK, TRAEFIK, name="wiki.localhost"))
        assert "WARN" not in out


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


class TestUnchanged:
    def test_lets_encrypt_is_ok(self):
        out = _report(_probe(LE, "CN=wiki.example.org"))
        assert "OK (expires in 60 days, issuer %s)" % LE in out
        assert "WARN" not in out

    def test_staging_still_warns_as_staging(self):
        out = _report(_probe(STAGING, "CN=wiki.example.org"))
        assert "WARN — staging certificate" in out
        assert "self-signed" not in out

    def test_tls_internal_stays_ok(self):
        out = _report(_probe(
            CADDY_LOCAL, "", tls_internal="/srv/site/config/Caddyfile.site"))
        assert "'tls internal' is configured" in out
        assert "WARN" not in out
