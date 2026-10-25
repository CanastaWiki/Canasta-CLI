"""Run the rendered edge configuration in the pinned HAProxy image.

Each backend's first rule is replaced with a response naming the backend, so
a request shows which instance and class the edge chose without any Caddy
behind it. Skipped where Docker is not available.
"""

import http.client
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
import uuid

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID

import canasta_edge_render as edge

# Code under test in other modules exports DOCKER_HOST into os.environ, and
# not every test undoes it. Pytest imports every test module before running
# any, so the environment here is the one the suite started with.
_DOCKER_ENV = dict(os.environ)


def _docker(*args, **kwargs):
    return subprocess.run(["docker"] + list(args), env=_DOCKER_ENV,
                          capture_output=True, text=True, **kwargs)


def _docker_ok():
    return bool(shutil.which("docker")) and _docker("info").returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="Docker is not available")


def _instances():
    return [
        edge.instance_from_config(
            "main", {}, [{"id": "w0", "url": "example.com"},
                         {"id": "w1", "url": "www.example.com/docs"}],
            [{"hostname": "images.example.com", "bucket": "b", "endpoint": "e"}]),
        edge.instance_from_config(
            "other", {"TLS_MODE": "custom"},
            [{"id": "w0", "url": "other.example.org"}]),
        edge.instance_from_config(
            "limited", {"CANASTA_EDGE_RATE_DEFAULT": "3"},
            [{"id": "w0", "url": "limited.example.org"}],
            [{"hostname": "img.limited.example.org", "bucket": "b", "endpoint": "e"}]),
        edge.instance_from_config(
            "denied", {}, [{"id": "w0", "url": "denied.example.org"}],
            deny_user_agents="# AI crawlers\nGPTBot\n", deny_cidrs="203.0.113.0/24\n"),
        edge.instance_from_config(
            "walled", {}, [{"id": "w0", "url": "walled.example.org"}],
            deny_cidrs="0.0.0.0/0\n::/0\n"),
        edge.instance_from_config(
            "cdn", {"TRUSTED_PROXIES": "cloudflare", "CANASTA_EDGE_RATE_DEFAULT": "3"},
            [{"id": "w0", "url": "cdn.example.org"}]),
        edge.instance_from_config(
            "proxied", {"TRUSTED_PROXIES": "imperva@0.0.0.0/0,::/0",
                        "CANASTA_EDGE_RATE_DEFAULT": "3"},
            [{"id": "w0", "url": "proxied.example.org"}]),
        edge.instance_from_config(
            "unlisted", {"TRUSTED_PROXIES": "imperva@203.0.113.10/32",
                         "CANASTA_EDGE_RATE_DEFAULT": "3"},
            [{"id": "w0", "url": "unlisted.example.org"}]),
        edge.instance_from_config(
            "mapped", {"TRUSTED_PROXIES": "cloudflare", "CANASTA_EDGE_RATE_DEFAULT": "3",
                       "MW_SITEMAP_URL_PATH": "/wiki/sitemap"},
            [{"id": "w0", "url": "mapped.example.org"},
             {"id": "w1", "url": "mapped.example.org/en"}]),
    ]


def _stub_backends(cfg):
    """Answer with the backend's name where its Caddy would be reached:
    after the deny and rate rules, so they still apply."""
    out, backend = [], None
    for line in cfg.splitlines():
        match = re.match(r"^backend (be_\w+?_(?:dynamic|default|media))$", line)
        if match:
            backend = match.group(1)
        elif line.startswith("backend "):
            backend = None
        if backend and "if { nbsrv eq 0 }" in line:
            out.append('    http-request return status 200 content-type text/plain '
                       'string "%s"' % backend)
        out.append(line)
    return "\n".join(out) + "\n"


def _self_signed(path, common_name):
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-subj", "/CN=%s" % common_name, "-keyout", path + ".key",
         "-out", path + ".crt"],
        check=True, capture_output=True)
    with open(path, "w") as out:
        for part in (".crt", ".key"):
            with open(path + part) as f:
                out.write(f.read())
            os.remove(path + part)


def _port(container, inner):
    out = _docker("port", container, "%d/tcp" % inner, check=True).stdout
    return int(out.splitlines()[0].rsplit(":", 1)[1])


@pytest.fixture(scope="module")
def edge_ports(tmp_path_factory):
    conf = tmp_path_factory.mktemp("edge")
    rendered = edge.render(_instances(), acme_thumbprint="THUMB",
                           cdn_ranges={"cloudflare": ["0.0.0.0/0", "::/0"]})
    for name, content in rendered["files"].items():
        if name == "haproxy.cfg":
            content = _stub_backends(content)
        (conf / name).write_text(content)
    certs = conf / "certs"
    certs.mkdir()
    _self_signed(str(certs / "default.pem"), "default")
    for entry in rendered["certificates"]:
        _self_signed(str(certs / entry["file"]), entry["file"])
    os.chmod(conf, 0o755)
    for path in [certs] + list(certs.iterdir()) + list(conf.iterdir()):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)

    name = "canasta-edge-test-%s" % uuid.uuid4().hex[:8]
    started = _docker(
        "run", "-d", "--rm", "--name", name,
        "-p", "127.0.0.1::80", "-p", "127.0.0.1::443",
        "--tmpfs", "/var/run/haproxy:mode=1777",
        "-v", "%s:%s:ro" % (conf, edge.CONFIG_DIR), edge.HAPROXY_IMAGE)
    if started.returncode != 0:
        pytest.fail("docker run failed:\n" + started.stderr)
    try:
        ports = {80: _port(name, 80), 443: _port(name, 443)}
        deadline = time.time() + 30
        while True:
            try:
                socket.create_connection(("127.0.0.1", ports[443]), timeout=1).close()
                _request(ports, "example.com", "/")
                break
            except (OSError, http.client.HTTPException):
                if time.time() > deadline:
                    logs = _docker("logs", name)
                    pytest.fail("edge did not start:\n" + logs.stdout + logs.stderr)
                time.sleep(0.5)
        yield ports
    finally:
        _docker("rm", "-f", name)


def _context():
    # The certificates are self-signed, so they are not verified; the test
    # checks which one the edge chose.
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _request(ports, host, path, method="GET", headers=None, tls=True, sni=None):
    if tls:
        conn = http.client.HTTPSConnection(
            "127.0.0.1", ports[443], timeout=5, context=_context())
        conn.sock = _context().wrap_socket(
            socket.create_connection(("127.0.0.1", ports[443]), timeout=5),
            server_hostname=sni or host.split(":")[0])
    else:
        conn = http.client.HTTPConnection("127.0.0.1", ports[80], timeout=5)
    conn.request(method, path, headers=dict({"Host": host}, **(headers or {})))
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body, resp.getheader("Location")


@pytest.mark.parametrize("host,path,method,headers,expected", [
    ("example.com", "/wiki/Main_Page", "GET", {}, "be_main_default"),
    ("example.com", "/w/index.php?title=Main_Page&action=view", "GET", {}, "be_main_default"),
    ("example.com", "/w/images/a/ab/Logo.png", "GET", {}, "be_main_default"),
    ("example.com", "/wiki/Main_Page", "GET", {"Cookie": "lang=en"}, "be_main_default"),
    ("example.com", "/w/api.php?action=query", "GET", {}, "be_main_dynamic"),
    ("example.com", "/w/rest.php/v1/page/Main_Page", "GET", {}, "be_main_dynamic"),
    ("example.com", "/wiki/Special:RecentChanges", "GET", {}, "be_main_dynamic"),
    ("example.com", "/wiki/special:recentchanges", "GET", {}, "be_main_dynamic"),
    ("example.com", "/w/index.php?title=Special%3AWhatLinksHere", "GET", {}, "be_main_dynamic"),
    ("example.com", "/w/index.php?title=Main_Page&action=history", "GET", {}, "be_main_dynamic"),
    ("example.com", "/w/index.php?title=Main_Page&oldid=5", "GET", {}, "be_main_dynamic"),
    ("example.com", "/w/index.php?title=Main_Page&diff=5", "GET", {}, "be_main_dynamic"),
    ("example.com", "/wiki/Main_Page", "POST", {}, "be_main_dynamic"),
    ("example.com", "/wiki/Main_Page", "HEAD", {}, "be_main_default"),
    ("example.com", "/wiki/Main_Page", "GET", {"Cookie": "wiki_session=abc"}, "be_main_dynamic"),
    ("example.com", "/wiki/Main_Page", "GET", {"Cookie": "wikiToken=abc"}, "be_main_dynamic"),
    ("example.com", "/wiki/Main_Page", "GET", {"Authorization": "Bearer x"}, "be_main_dynamic"),
    ("example.com", "/w/images/thumb/a/ab/Logo.png/300px-Logo.png", "GET",
     {"Cookie": "wiki_session=abc"}, "be_main_default"),
    ("example.com", "/w/resources/src/sort_both.svg?0e440", "GET",
     {"Authorization": "Basic eDp4"}, "be_main_default"),
    ("example.com", "/w/skins/Vector/resources/print.css", "GET",
     {"Cookie": "wiki_session=abc"}, "be_main_default"),
    ("example.com", "/w/extensions/Foo/modules/foo.js", "GET",
     {"Cookie": "wikiToken=abc"}, "be_main_default"),
    ("example.com", "/w/extensions/Foo/entry.php", "GET",
     {"Cookie": "wiki_session=abc"}, "be_main_dynamic"),
    ("example.com", "/w/load.php?modules=jquery", "GET",
     {"Cookie": "wiki_session=abc"}, "be_main_dynamic"),
    ("example.com", "/w/images/a/ab/Logo.png", "POST", {"Cookie": "wiki_session=abc"},
     "be_main_dynamic"),
    ("EXAMPLE.COM", "/wiki/Main_Page", "GET", {}, "be_main_default"),
    ("example.com:443", "/wiki/Main_Page", "GET", {}, "be_main_default"),
    ("www.example.com", "/docs/wiki/Main_Page", "GET", {}, "be_main_default"),
    ("images.example.com", "/a/ab/Logo.png", "GET", {"Cookie": "wiki_session=abc"}, "be_main_media"),
    ("other.example.org", "/wiki/Special:Log", "GET", {}, "be_other_dynamic"),
    ("other.example.org", "/wiki/Main_Page", "GET", {}, "be_other_default"),
])
def test_requests_reach_the_expected_backend(edge_ports, host, path, method, headers, expected):
    status, body, _ = _request(edge_ports, host, path, method, headers)
    assert status == 200
    if method != "HEAD":
        assert body == expected


def test_unknown_host_gets_404(edge_ports):
    status, body, _ = _request(edge_ports, "unknown.example.net", "/")
    assert status == 404 and "No site" in body


def test_plain_http_redirects_to_https(edge_ports):
    status, _, location = _request(edge_ports, "example.com", "/wiki/X?a=1", tls=False)
    assert status == 301 and location == "https://example.com/wiki/X?a=1"


@pytest.mark.parametrize("tls", [False, True])
def test_acme_challenge_is_answered_on_both_ports(edge_ports, tls):
    status, body, _ = _request(
        edge_ports, "example.com", "/.well-known/acme-challenge/TOKEN123", tls=tls)
    assert status == 200 and body == "TOKEN123.THUMB\n"


@pytest.mark.parametrize("sni,expected", [
    ("example.com", "example.com.pem"),
    ("images.example.com", "images.example.com.pem"),
    ("other.example.org", "custom-other.pem"),
    ("unknown.example.net", "default"),
])
def test_certificate_is_chosen_by_sni(edge_ports, sni, expected):
    raw = _context().wrap_socket(
        socket.create_connection(("127.0.0.1", edge_ports[443]), timeout=5),
        server_hostname=sni)
    der = raw.getpeercert(binary_form=True)
    raw.close()
    cert = x509.load_der_x509_certificate(der)
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == expected


# --- mitigation ------------------------------------------------------------- #

def test_a_private_address_is_not_rate_limited(edge_ports):
    # The test client reaches the edge from the Docker network's gateway,
    # as a wiki calling its own site through gateway.docker.internal does.
    # A public client over the same rate is refused: see the CDN test below.
    codes = [_request(edge_ports, "limited.example.org", "/wiki/P%d" % n)[0]
             for n in range(5)]
    assert codes == [200] * 5


def test_denied_user_agents_match_anywhere_in_the_header(edge_ports):
    ua = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.1)"
    assert _request(edge_ports, "denied.example.org", "/", headers={"User-Agent": ua})[0] == 403
    assert _request(edge_ports, "denied.example.org", "/",
                    headers={"User-Agent": "Mozilla/5.0 Firefox/130.0"})[0] == 200


def test_denied_ranges_are_refused(edge_ports):
    assert _request(edge_ports, "walled.example.org", "/")[0] == 403


def test_deny_lists_are_per_instance(edge_ports):
    ua = "GPTBot/1.1"
    assert _request(edge_ports, "example.com", "/wiki/Main_Page",
                    headers={"User-Agent": ua})[0] == 200


def test_behind_a_cdn_limits_count_the_client_not_the_cdn(edge_ports):
    first = [_request(edge_ports, "cdn.example.org", "/wiki/A%d" % n,
                      headers={"CF-Connecting-IP": "198.51.100.7"})[0] for n in range(5)]
    other = _request(edge_ports, "cdn.example.org", "/wiki/B",
                     headers={"CF-Connecting-IP": "198.51.100.8"})[0]
    assert first[-1] == 429 and other == 200


def test_behind_listed_proxies_limits_count_the_cdn_client(edge_ports):
    first = [_request(edge_ports, "proxied.example.org", "/wiki/A%d" % n,
                      headers={"Incap-Client-IP": "198.51.100.11",
                               "X-Forwarded-For": "198.51.100.11, 198.51.100.200"})[0]
             for n in range(5)]
    other = _request(edge_ports, "proxied.example.org", "/wiki/B",
                     headers={"Incap-Client-IP": "198.51.100.12",
                              "X-Forwarded-For": "198.51.100.12, 198.51.100.200"})[0]
    assert first[-1] == 429 and other == 200


def test_a_cdn_header_from_an_unlisted_peer_is_ignored(edge_ports):
    # Believed, the forged public address would be rate-limited; ignored,
    # the client stays the test client's private address, which is not.
    codes = [_request(edge_ports, "unlisted.example.org", "/wiki/F%d" % n,
                      headers={"Incap-Client-IP": "198.51.100.13"})[0]
             for n in range(5)]
    assert codes == [200] * 5


def test_static_files_are_not_rate_limited(edge_ports):
    """A page with many images on the wiki's own hostname must not trip the
    per-client limit, and its files still load once page views are refused."""
    client = {"CF-Connecting-IP": "198.51.100.9"}
    files = ["/w/images/thumb/a/ab/P%d.jpg/300px-P%d.jpg" % (n, n) for n in range(6)] + [
        "/w/resources/src/a.svg", "/w/skins/Vector/a.css", "/w/extensions/Foo/a.js",
        "/public_assets/logo.png", "/favicon.ico"]
    assert [_request(edge_ports, "cdn.example.org", p, headers=client)[0]
            for p in files] == [200] * len(files)
    pages = [_request(edge_ports, "cdn.example.org", "/wiki/C%d" % n, headers=client)[0]
             for n in range(5)]
    assert pages[:3] == [200] * 3 and pages[-1] == 429
    assert _request(edge_ports, "cdn.example.org", "/public_assets/logo.png",
                    headers=client)[0] == 200


def test_a_configured_sitemap_path_is_static_for_its_instance_only(edge_ports):
    client = {"CF-Connecting-IP": "198.51.100.14"}
    files = ["/wiki/sitemap/sitemap-w0-NS_0-%d.xml.gz" % n for n in range(4)] + [
        "/en/wiki/sitemap/sitemap-index-mediawiki.xml"] * 2
    assert [_request(edge_ports, "mapped.example.org", p, headers=client)[0]
            for p in files] == [200] * len(files)
    assert _request(edge_ports, "mapped.example.org", "/wiki/sitemap/a.xml",
                    headers=dict(client, Cookie="wiki_session=abc"))[1] == "be_mapped_default"
    elsewhere = {"CF-Connecting-IP": "198.51.100.15"}
    codes = [_request(edge_ports, "cdn.example.org", "/wiki/sitemap/a%d.xml" % n,
                      headers=elsewhere)[0] for n in range(5)]
    assert codes[-1] == 429


def test_php_under_a_static_path_is_rate_limited(edge_ports):
    client = {"CF-Connecting-IP": "198.51.100.10"}
    codes = [_request(edge_ports, "cdn.example.org", "/w/extensions/Foo/entry.php",
                      headers=client)[0] for n in range(5)]
    assert codes[-1] == 429


def test_a_request_body_is_accepted(edge_ports):
    assert _request(edge_ports, "example.com", "/w/index.php", method="POST",
                    headers={"Content-Type": "text/plain"})[0] == 200


def test_media_requests_are_not_rate_limited(edge_ports):
    """A page with many images must not trip the per-client limit."""
    codes = [_request(edge_ports, "img.limited.example.org", "/thumb/%d.png" % n)[0]
             for n in range(8)]
    assert codes == [200] * 8
