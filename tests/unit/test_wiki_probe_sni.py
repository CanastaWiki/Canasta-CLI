"""The local wiki probe has to present the wiki's own domain in TLS SNI.

`canasta list --check-wikis` reaches an instance through the port it
publishes on this machine. It used to do that by rewriting the URL to
https://localhost:<port> and carrying the domain in a Host header — but
SNI comes from the URL's hostname, so the server received SNI 'localhost',
had no certificate for it, and aborted the handshake. A healthy wiki was
reported NOT REACHABLE.

The server here models that: it answers only when SNI names its
certificate, and sends the same alert Caddy does otherwise.
"""

import datetime
import http.server
import os
import ssl
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from direct_commands import _helpers  # noqa: E402

# Deliberately not resolvable: the probe must reach the server by
# forcing the socket to loopback, never by looking the name up.
WIKI_DOMAIN = "canasta-probe.test"

SITEINFO_BODY = b'{"batchcomplete":"","query":{"general":{"sitename":"C"}}}'


def _self_signed_cert(tmp_path, common_name):
    """A cert/key pair for common_name, as (certfile, keyfile) paths."""
    cryptography = pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    del cryptography
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2100, 1, 1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    certfile = str(tmp_path / "cert.pem")
    keyfile = str(tmp_path / "key.pem")
    with open(certfile, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(keyfile, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    return certfile, keyfile


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.server.host_headers.append(self.headers.get("Host"))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(SITEINFO_BODY)))
        self.end_headers()
        self.wfile.write(SITEINFO_BODY)

    def log_message(self, *args):
        pass


@pytest.fixture
def tls_wiki(tmp_path):
    """A TLS server on loopback that serves only its own domain's SNI."""
    certfile, keyfile = _self_signed_cert(tmp_path, WIKI_DOMAIN)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile, keyfile)

    seen_sni = []

    def _record_sni(sock, servername, ctx):
        seen_sni.append(servername)
        if servername != WIKI_DOMAIN:
            # What Caddy sends when it holds no certificate for the name:
            # "tlsv1 alert internal error", before any header is read.
            return ssl.ALERT_DESCRIPTION_INTERNAL_ERROR
        return None

    context.sni_callback = _record_sni

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.host_headers = []
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, seen_sni
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _probe(monkeypatch, port, scheme="https"):
    monkeypatch.setattr(
        _helpers, "_read_env_file",
        lambda path, host: {"HTTPS_PORT": str(port), "HTTP_PORT": str(port)},
    )
    url = "%s://%s%s" % (scheme, WIKI_DOMAIN, _helpers._SITEINFO_QUERY)
    return _helpers._probe_wiki_local(url, "/some/instance")


class TestProbeSendsTheWikiDomainAsSNI:
    def test_wiki_serving_its_own_domain_is_reachable(self, tls_wiki, monkeypatch):
        server, seen_sni = tls_wiki
        port = server.server_address[1]

        assert _probe(monkeypatch, port) is True
        assert seen_sni == [WIKI_DOMAIN]

    def test_host_header_still_names_the_wiki(self, tls_wiki, monkeypatch):
        server, _ = tls_wiki
        port = server.server_address[1]

        assert _probe(monkeypatch, port) is True
        assert server.host_headers == [WIKI_DOMAIN]

    def test_the_server_rejects_localhost_sni(self, tls_wiki, monkeypatch):
        """Guards the fixture: it has to model the failure being fixed."""
        server, seen_sni = tls_wiki
        port = server.server_address[1]

        url = "https://localhost:%d%s" % (port, _helpers._SITEINFO_QUERY)
        assert _helpers._probe_wiki_local(url, "") is False
        assert seen_sni == ["localhost"]


class TestProbePortSelection:
    def test_unparsable_port_falls_back_to_resolving_the_url(self, monkeypatch):
        """An .env with no usable port must not force a bogus connection."""
        monkeypatch.setattr(
            _helpers, "_read_env_file", lambda path, host: {"HTTPS_PORT": ""},
        )
        calls = []
        monkeypatch.setattr(
            _helpers, "_fetch_is_mediawiki",
            lambda url, loopback_port=None: calls.append(loopback_port) or False,
        )
        _helpers._probe_wiki_local(
            "https://%s/w/api.php" % WIKI_DOMAIN, "/some/instance")
        assert calls == [None]
