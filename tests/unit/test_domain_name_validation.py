"""Tests for --domain-name validation guidance (#667).

The hostname validator rejects a value that carries a scheme or a port,
but the bare "not a valid hostname" message left users (following older
docs / the prior Go CLI) guessing. The validator now appends a hint that
points them at HTTP_PORT/HTTPS_PORT for ports and a bare hostname for
--domain-name.
"""

import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402


class TestHostnameHint:
    def test_hint_fires_on_scheme(self):
        hint = canasta._hostname_hint("https://example.com")
        assert "bare hostname" in hint
        assert "HTTP_PORT/HTTPS_PORT" in hint

    def test_hint_fires_on_port(self):
        hint = canasta._hostname_hint("example.com:8443")
        assert "HTTP_PORT/HTTPS_PORT" in hint
        assert "CADDY_AUTO_HTTPS=off" in hint

    def test_hint_fires_on_localhost_port(self):
        assert canasta._hostname_hint("localhost:8443") != ""

    def test_no_hint_for_plain_bad_hostname(self):
        # A genuinely malformed hostname (uppercase, comma) is not a
        # port/scheme problem, so no port hint should be appended.
        assert canasta._hostname_hint("Example,com") == ""
        assert canasta._hostname_hint("UPPER") == ""


class TestHostnameValidatorWiring:
    def test_hostname_validator_has_hint_fn(self):
        validator = canasta._VALIDATORS["hostname"]
        assert len(validator) == 3, (
            "hostname validator must carry a hint function as its 3rd element"
        )
        assert callable(validator[2])
        # A host:port value passes the regex check? It must NOT — the regex
        # rejects ':' — and the hint must then describe where ports go.
        regex, _template, hint_fn = validator
        assert not regex.match("example.com:8443")
        assert "HTTP_PORT/HTTPS_PORT" in hint_fn("example.com:8443")
