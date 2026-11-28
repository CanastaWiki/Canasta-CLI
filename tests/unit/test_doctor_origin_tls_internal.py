"""An internally-issued certificate is only a problem if nobody asked for it.

TLS_MODE=internal (or a TLS_MODE_HOSTS entry) is a deliberate choice —
typically behind a CDN that terminates public TLS — and the edge then never
asks Let's Encrypt. Reporting every internal certificate on a public hostname
as a failure accuses a correctly configured instance of an outage it does not
have. The diagnosis is also printed once, not per domain, so a farm's report
is not one paragraph copied N times.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from direct_commands import doctor

NOW = datetime.datetime(2026, 8, 21, tzinfo=datetime.timezone.utc)
INTERNAL = "CN=Canasta edge CA"
PUBLIC = "CN=R3, O=Let's Encrypt, C=US"
LATER = "Nov 20 12:00:00 2026 GMT"


def _lines(entries, internal_names=None):
    return "\n".join(doctor._origin_tls_lines_from_entries(
        "inst", entries, NOW, internal_names=internal_names))


class TestDeliberateInternalCA:
    def test_configured_internal_mode_is_reported_as_intended(self):
        out = _lines([("wiki.example.com", INTERNAL, LATER)],
                     {"wiki.example.com": "TLS_MODE=internal"})
        assert "OK" in out and "TLS_MODE=internal is configured" in out
        assert "WARN" not in out

    def test_without_the_setting_it_still_warns(self):
        out = _lines([("wiki.example.com", INTERNAL, LATER)])
        assert "WARN" in out and "no Let's Encrypt certificate" in out

    def test_the_warning_says_how_to_declare_it_intentional(self):
        out = _lines([("wiki.example.com", INTERNAL, LATER)])
        assert "set TLS_MODE=internal" in out


class TestTheDiagnosisIsPrintedOnce:
    def test_several_names_share_one_explanation(self):
        out = _lines([
            ("a.example.com", INTERNAL, LATER),
            ("b.example.com", INTERNAL, LATER),
            ("c.example.com", INTERNAL, LATER),
        ])
        assert out.count("no Let's Encrypt certificate") == 1
        for name in ("a.example.com", "b.example.com", "c.example.com"):
            assert name in out

    def test_a_healthy_certificate_is_unaffected(self):
        out = _lines([("wiki.example.com", PUBLIC, LATER)])
        assert "WARN" not in out

    def test_a_local_name_is_still_expected_to_be_internal(self):
        out = _lines([("localhost", INTERNAL, LATER)])
        assert "OK" in out and "WARN" not in out


def test_the_probe_reads_no_caddy_config():
    assert "TLSINTERNAL" not in doctor._ORIGIN_TLS_SCRIPT
    assert "%(cfg)s" not in doctor._ORIGIN_TLS_SCRIPT
