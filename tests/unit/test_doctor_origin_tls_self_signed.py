"""`canasta doctor` flags self-signed origin certificates."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from direct_commands import doctor


NOW = datetime.datetime(2026, 7, 31, tzinfo=datetime.timezone.utc)
LATER = (NOW + datetime.timedelta(days=60)).strftime("%b %e %H:%M:%S %Y GMT")
D = doctor._helpers._SENTINEL

LE = "C=US, O=Let's Encrypt, CN=R11"
STAGING = "C=US, O=(STAGING) Let's Encrypt, CN=(STAGING) Artificial Amaranth YE1"
EDGE_CA = "CN=Canasta edge CA"
SELF = "CN=wiki.example.org"


def _probe(issuer, subject, name="wiki.example.org"):
    return ("%sNAME:%s\nsubject=%s\nissuer=%s\nnotAfter=%s\n"
            % (D, name, subject, issuer, LATER))


def _report(stdout, internal_names=None):
    return "\n".join(doctor._origin_tls_lines_from_entries(
        "site", doctor._parse_origin_tls(stdout), NOW,
        internal_names=internal_names))


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
        assert "TLS_CERT" in out

    def test_self_signed_on_a_local_name_is_exempt(self):
        out = _report(_probe(
            "CN=wiki.localhost", "CN=wiki.localhost", name="wiki.localhost"))
        assert "WARN" not in out


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
        out = _report(_probe(EDGE_CA, ""),
                      {"wiki.example.org": "TLS_MODE=internal"})
        assert "TLS_MODE=internal is configured" in out
        assert "WARN" not in out
