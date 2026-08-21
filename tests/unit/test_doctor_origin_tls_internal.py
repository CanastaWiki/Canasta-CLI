"""An internally-issued certificate is only a problem if nobody asked for it.

`tls internal` is a deliberate choice — typically behind a CDN that
terminates public TLS — and it stops Caddy contacting ACME at all. The
check used to report every internal certificate on a public hostname as
"ACME is failing", which accuses a correctly configured instance of an
outage it does not have. It also repeated the whole diagnosis per domain,
so a farm's report was mostly one paragraph copied N times.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from direct_commands import _helpers  # noqa: E402
from direct_commands import doctor  # noqa: E402

NOW = datetime.datetime(2026, 8, 21, tzinfo=datetime.timezone.utc)
INTERNAL = "CN=Caddy Local Authority - ECC Intermediate"
PUBLIC = "CN=R3, O=Let's Encrypt, C=US"
LATER = "Nov 20 12:00:00 2026 GMT"


def _lines(entries, tls_internal=False):
    return "\n".join(doctor._origin_tls_lines_from_entries(
        "inst", entries, NOW, tls_internal=tls_internal))


class TestDeliberateInternalCA:
    def test_configured_tls_internal_is_reported_as_intended(self):
        out = _lines([("wiki.example.com", INTERNAL, LATER)],
                     tls_internal=True)
        assert "OK" in out and "tls internal" in out
        assert "WARN" not in out, (
            "the operator asked for the internal CA; ACME is never contacted")
        assert "ACME is failing" not in out

    def test_without_the_directive_it_still_warns(self):
        out = _lines([("wiki.example.com", INTERNAL, LATER)],
                     tls_internal=False)
        assert "WARN" in out and "ACME is failing" in out

    def test_the_warning_says_how_to_declare_it_intentional(self):
        out = _lines([("wiki.example.com", INTERNAL, LATER)])
        assert "tls internal" in out and "Caddyfile.site" in out


class TestTheDiagnosisIsPrintedOnce:
    def test_several_names_share_one_explanation(self):
        out = _lines([
            ("a.example.com", INTERNAL, LATER),
            ("b.example.com", INTERNAL, LATER),
            ("c.example.com", INTERNAL, LATER),
        ])
        assert out.count("ACME is failing") == 1, (
            "the diagnosis is identical for every name; repeating it per "
            "domain buries the rest of the report")
        for name in ("a.example.com", "b.example.com", "c.example.com"):
            assert name in out

    def test_a_healthy_certificate_is_unaffected(self):
        out = _lines([("wiki.example.com", PUBLIC, LATER)])
        assert "WARN" not in out and "ACME is failing" not in out

    def test_a_local_name_is_still_expected_to_be_internal(self):
        out = _lines([("localhost", INTERNAL, LATER)])
        assert "OK" in out and "WARN" not in out


class TestProbeMarker:
    def _stdout(self, marker_value):
        d = _helpers._SENTINEL
        return "%sTLSINTERNAL:%s\n%sNAME:wiki.example.com\nissuer=%s\n" % (
            d, marker_value, d, INTERNAL)

    def test_a_matched_file_means_configured(self):
        assert doctor._parse_tls_internal(
            self._stdout("/srv/inst/config/Caddyfile.site")) is True

    def test_no_match_means_not_configured(self):
        assert doctor._parse_tls_internal(self._stdout("")) is False

    def test_the_marker_is_not_mistaken_for_a_probed_name(self):
        entries = doctor._parse_origin_tls(
            self._stdout("/srv/inst/config/Caddyfile.site"))
        assert [e[0] for e in entries] == ["wiki.example.com"]
