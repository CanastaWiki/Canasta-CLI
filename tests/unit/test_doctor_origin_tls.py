"""Tests for `canasta doctor`'s origin-certificate check."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from direct_commands import doctor  # noqa: E402


NOW = datetime.datetime(2026, 7, 31, tzinfo=datetime.timezone.utc)
LE = "C = US, O = Let's Encrypt, CN = R11"
CADDY_LOCAL = "CN = Caddy Local Authority - ECC Intermediate"


def _fmt(dt):
    """openssl's notAfter rendering."""
    return dt.strftime("%b %e %H:%M:%S %Y GMT")


def _lines(entries, now=NOW):
    return doctor._origin_tls_lines_from_entries("site", entries, now)


class TestPublicHostname:
    def test_public_domains(self):
        assert doctor._is_public_hostname("wiki.example.org")
        assert doctor._is_public_hostname("Example.COM.")

    def test_local_and_reserved_names_are_not_public(self):
        for name in ("localhost", "wiki.localhost", "box.local", "svc.internal",
                     "a.test", "x.example", "y.invalid", "nodots"):
            assert not doctor._is_public_hostname(name), name

    def test_ip_literals_are_not_public(self):
        assert not doctor._is_public_hostname("192.0.2.10")
        assert not doctor._is_public_hostname("2001:db8::1")

    def test_empty(self):
        assert not doctor._is_public_hostname("")
        assert not doctor._is_public_hostname(None)


class TestParseProbeOutput:
    def test_missing_openssl_returns_none(self):
        assert doctor._parse_origin_tls("NO_OPENSSL\n") is None

    def test_parses_issuer_and_expiry_per_name(self):
        d = doctor._helpers._SENTINEL
        out = (
            "%sNAME:a.example.org\nissuer=%s\nnotAfter=Sep 10 12:00:00 2026 GMT\n"
            "%sNAME:b.example.org\nissuer=%s\nnotAfter=Aug  1 00:00:00 2026 GMT\n"
            % (d, LE, d, CADDY_LOCAL))
        assert doctor._parse_origin_tls(out) == [
            ("a.example.org", LE, "Sep 10 12:00:00 2026 GMT"),
            ("b.example.org", CADDY_LOCAL, "Aug  1 00:00:00 2026 GMT"),
        ]

    def test_name_with_no_handshake_yields_empty_fields(self):
        d = doctor._helpers._SENTINEL
        assert doctor._parse_origin_tls("%sNAME:a.example.org\n" % d) == [
            ("a.example.org", "", ""),
        ]


class TestExpiryParsing:
    def test_openssl_format(self):
        assert doctor._parse_cert_expiry("Sep 10 12:00:00 2026 GMT") == (
            datetime.datetime(2026, 9, 10, 12, tzinfo=datetime.timezone.utc))

    def test_day_padded_with_a_space(self):
        assert doctor._parse_cert_expiry("Aug  1 00:00:00 2026 GMT") == (
            datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc))

    def test_garbage(self):
        assert doctor._parse_cert_expiry("not a date") is None
        assert doctor._parse_cert_expiry("") is None


class TestReportedLines:
    def test_healthy_certificate_is_ok(self):
        body = " ".join(_lines([
            ("a.example.org", LE, _fmt(NOW + datetime.timedelta(days=61)))]))
        assert "WARN" not in body
        assert "OK (expires in 61 days" in body

    def test_expired_certificate_warns_with_age(self):
        body = " ".join(_lines([
            ("a.example.org", LE, _fmt(NOW - datetime.timedelta(days=9)))]))
        assert "WARN" in body
        assert "EXPIRED 9 day(s) ago" in body

    def test_long_expired_certificate_warns(self):
        body = " ".join(_lines([
            ("a.example.org", LE, _fmt(NOW - datetime.timedelta(days=400)))]))
        assert "EXPIRED 400 day(s) ago" in body

    def test_unrenewed_inside_the_renewal_window_warns(self):
        body = " ".join(_lines([
            ("a.example.org", LE, _fmt(NOW + datetime.timedelta(days=5)))]))
        assert "WARN" in body
        assert "expires in 5 day(s)" in body
        assert "broken ACME path" in body

    def test_internal_ca_on_a_public_domain_warns(self):
        body = " ".join(_lines([
            ("a.example.org", CADDY_LOCAL,
             _fmt(NOW + datetime.timedelta(days=1)))]))
        assert "WARN" in body
        assert "internal CA" in body

    def test_internal_ca_on_localhost_is_not_a_warning(self):
        body = " ".join(_lines([
            ("localhost", CADDY_LOCAL,
             _fmt(NOW + datetime.timedelta(days=1)))]))
        assert "WARN" not in body

    def test_per_domain_divergence_on_one_host(self):
        primary = "primary.example.org"
        lines = _lines([
            ("a.example.org", LE, _fmt(NOW + datetime.timedelta(days=61))),
            ("b.example.org", LE, _fmt(NOW + datetime.timedelta(days=61))),
            (primary, CADDY_LOCAL, _fmt(NOW + datetime.timedelta(days=1))),
        ])
        warns = [ln for ln in lines if "WARN" in ln]
        assert len(warns) == 1
        assert warns[0].strip().startswith(primary + ":")

    def test_no_listener_is_reported_not_warned(self):
        body = " ".join(_lines([("a.example.org", "", "")]))
        assert "WARN" not in body
        assert "no certificate served" in body

    def test_missing_openssl_is_reported_as_skipped(self):
        body = " ".join(_lines(None))
        assert "SKIPPED" in body
        assert "openssl" in body

    def test_no_names_produces_no_section(self):
        assert _lines([]) == []


class TestServerNameDerivation:
    def test_strips_path_and_port_and_dedupes(self, monkeypatch):
        monkeypatch.setattr(
            doctor._helpers, "_read_wikis",
            lambda path, host: [
                {"url": "wiki.example.org/w"},
                {"url": "wiki.example.org:8443/other"},
                {"url": "second.example.org"},
                {"url": ""},
            ])
        assert doctor._origin_tls_server_names("/srv/x", "localhost") == [
            "wiki.example.org", "second.example.org"]


class TestOriginTlsGate:
    def _inst(self, **kw):
        inst = {"id": "site", "orchestrator": "compose", "path": "/srv/site",
                "host": "localhost"}
        inst.update(kw)
        return inst

    def test_skipped_when_compose_serves_plain_http(self, monkeypatch):
        monkeypatch.setattr(
            doctor._helpers, "_read_env_for",
            lambda inst, key: "off" if key == "CADDY_AUTO_HTTPS" else None)
        assert doctor._origin_tls_lines(self._inst()) == []

    def test_skipped_without_a_path(self):
        assert doctor._origin_tls_lines({"id": "site"}) == []

    def test_probes_the_configured_https_port(self, monkeypatch):
        seen = {}

        class _R:
            stdout = ""

        monkeypatch.setattr(
            doctor._helpers, "_read_env_for",
            lambda inst, key: {"HTTPS_PORT": "8443"}.get(key))
        monkeypatch.setattr(
            doctor._helpers, "_read_wikis",
            lambda path, host: [{"url": "a.example.org"}])
        monkeypatch.setattr(doctor._helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(
            doctor.subprocess, "run",
            lambda *a, **k: (seen.update(script=a[0][2]) or _R()))
        doctor._origin_tls_lines(self._inst())
        assert "127.0.0.1:'8443'" in seen["script"]
        assert "-servername" in seen["script"]

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

# Real issuer string from a Let's Encrypt staging certificate.
LE_STAGING = ("C=US, O=(STAGING) Let's Encrypt, "
              "CN=(STAGING) Artificial Amaranth YE1")


class TestStagingCertificates:
    """A staging certificate passes every other check and still breaks the site.

    It is unexpired and correctly issued, so the expiry branches are
    happy, but it chains to an untrusted root — browsers reject it just
    as they reject the internal-CA fallback. Reporting OK meant doctor
    passed an instance no visitor could load.
    """

    def test_a_staging_certificate_warns(self):
        body = " ".join(_lines([
            ("a.example.org", LE_STAGING,
             _fmt(NOW + datetime.timedelta(days=89)))]))
        assert "WARN" in body, (
            "a browser-untrusted staging certificate is reported as OK"
        )
        assert "staging certificate" in body

    def test_it_still_reports_the_remaining_days(self):
        body = " ".join(_lines([
            ("a.example.org", LE_STAGING,
             _fmt(NOW + datetime.timedelta(days=89)))]))
        assert "89 more day(s)" in body

    def test_it_names_the_way_out(self):
        body = " ".join(_lines([
            ("a.example.org", LE_STAGING,
             _fmt(NOW + datetime.timedelta(days=89)))]))
        assert "CANASTA_STAGING_CERTS" in body

    def test_an_expired_staging_certificate_still_reports_expiry(self):
        # Expiry is the more urgent fact; it is checked first.
        body = " ".join(_lines([
            ("a.example.org", LE_STAGING,
             _fmt(NOW - datetime.timedelta(days=3)))]))
        assert "EXPIRED 3 day(s) ago" in body

    def test_production_letsencrypt_is_unaffected(self):
        body = " ".join(_lines([
            ("a.example.org", LE, _fmt(NOW + datetime.timedelta(days=61)))]))
        assert "staging" not in body.lower()
        assert "OK (expires in 61 days" in body
