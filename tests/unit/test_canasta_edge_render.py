"""The HAProxy edge renderer: instance settings in, edge files out."""

import os
import re

import pytest

import canasta_edge_render as edge

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "edge")
VCL = os.path.join(REPO_ROOT, "instance_template", "config", "default.vcl")


def _inst(instance_id="wiki1", urls=("example.com",), env=None, media=(), snippet=""):
    wikis = [{"id": "w%d" % n, "url": u} for n, u in enumerate(urls)]
    return edge.instance_from_config(
        instance_id, env or {}, wikis,
        [{"hostname": h, "bucket": "b", "endpoint": "e"} for h in media], snippet)


def _two():
    return [
        _inst("main", ("example.com", "example.com/docs", "www.example.com"),
              media=("images.example.com",)),
        _inst("other", ("other.example.org",),
              {"CADDY_TLS_MODE": "custom", "PM_MAX_CHILDREN": "10",
               "CANASTA_ENABLE_VARNISH": "false"},
              snippet="# tighter\nhttp-request deny if { path_beg /private }\n"),
    ]


# --- instance_from_config -------------------------------------------------- #

def test_hostnames_come_from_wiki_urls_once_each():
    inst = _inst(urls=("Example.com", "example.com/docs", "docs.example.com/x"))
    assert inst["hostnames"] == ["example.com", "docs.example.com"]


def test_defaults_when_env_is_silent():
    inst = _inst()
    assert inst["tls_mode"] == "acme"
    assert inst["pm_max_children"] == 25
    assert inst["varnish"] is True
    assert inst["staging_certs"] is False
    assert (inst["tls_cert"], inst["tls_key"]) == ("origin.crt", "origin.key")


@pytest.mark.parametrize("url,fragment", [
    ("example.com:8443", "has a port"),
    ("localhost", "localhost"),
    ("wiki.localhost/x", "localhost"),
    ("192.0.2.10", "IP address"),
    ("*.example.com", "is a wildcard"),
    ("exa_mple.com", "is not a valid hostname"),
])
def test_unroutable_wiki_hosts_are_refused(url, fragment):
    with pytest.raises(edge.EdgeError, match=re.escape(fragment)):
        _inst(urls=(url,))


def test_media_host_that_is_also_a_wiki_host_is_refused():
    with pytest.raises(edge.EdgeError, match="more than once"):
        _inst(media=("example.com",))


def test_unknown_tls_mode_is_refused():
    with pytest.raises(edge.EdgeError, match="CADDY_TLS_MODE is 'acmee'"):
        _inst(env={"CADDY_TLS_MODE": "acmee"})


@pytest.mark.parametrize("value", ["0", "-3", "many"])
def test_bad_pm_max_children_is_refused(value):
    with pytest.raises(edge.EdgeError, match="PM_MAX_CHILDREN"):
        _inst(env={"PM_MAX_CHILDREN": value})


def test_instance_without_wikis_is_refused():
    with pytest.raises(edge.EdgeError, match="has no wikis"):
        edge.instance_from_config("wiki1", {}, [])


@pytest.mark.parametrize("line", [
    "backend evil", "frontend x", "  Global", "listen stats", "defaults",
])
def test_snippet_cannot_open_a_section(line):
    with pytest.raises(edge.EdgeError, match="opens a new section"):
        _inst(snippet="http-request deny\n%s\n" % line)


def test_snippet_comments_and_blanks_are_dropped():
    inst = _inst(snippet="\n# note\n  timeout server 30s  \n")
    assert inst["snippet"] == ["timeout server 30s"]


# --- limits ---------------------------------------------------------------- #

def test_limits_follow_pm_max_children():
    assert edge.backend_limits(_inst()) == (15, 200)
    assert edge.backend_limits(_inst(env={"PM_MAX_CHILDREN": "10"})) == (6, 80)


def test_without_varnish_the_default_cap_is_lower():
    inst = _inst(env={"PM_MAX_CHILDREN": "10", "CANASTA_ENABLE_VARNISH": "false"})
    assert edge.backend_limits(inst) == (6, 20)


def test_a_single_worker_still_gets_a_dynamic_slot():
    assert edge.backend_limits(_inst(env={"PM_MAX_CHILDREN": "1"}))[0] == 1


# --- render ---------------------------------------------------------------- #

def test_render_matches_golden_files():
    files = edge.render(_two(), acme_thumbprint="THUMB")["files"]
    for name, content in files.items():
        with open(os.path.join(FIXTURES, name)) as f:
            assert content == f.read(), name


def test_render_is_independent_of_instance_order():
    assert edge.render(_two()) == edge.render(list(reversed(_two())))


def test_no_challenge_rule_without_a_thumbprint():
    assert "acme-challenge" not in edge.render(_two())["files"]["haproxy.cfg"]


def test_challenge_is_answered_on_both_frontends():
    cfg = edge.render(_two(), acme_thumbprint="THUMB")["files"]["haproxy.cfg"]
    sections = re.split(r"\n(?=\S)", cfg)
    for name in ("frontend http", "frontend https"):
        section = next(s for s in sections if s.startswith(name))
        assert "acme-challenge" in section and "THUMB" in section, name


def test_a_hostname_served_by_two_instances_is_refused():
    with pytest.raises(edge.EdgeError, match="'a' and 'b' both serve example.com"):
        edge.render([_inst("a"), _inst("b")])


def test_a_duplicate_instance_id_is_refused():
    with pytest.raises(edge.EdgeError, match="appears twice"):
        edge.render([_inst("a"), _inst("a", ("other.example.org",))])


def test_certificates_per_hostname_and_per_custom_instance():
    certs = edge.render(_two())["certificates"]
    assert [c["file"] for c in certs] == [
        "example.com.pem", "www.example.com.pem", "images.example.com.pem",
        "custom-other.pem",
    ]
    assert certs[-1]["hostnames"] == ["other.example.org"]
    assert certs[-1]["cert"] == "origin.crt"


def test_global_maxconn_has_a_floor():
    cfg = edge.render([_inst()])["files"]["haproxy.cfg"]
    assert "    maxconn %d\n" % edge.GLOBAL_MAXCONN_FLOOR in cfg


# --- Varnish parity -------------------------------------------------------- #

# Paths Varnish passes that Apache serves without PHP.
STATIC_PASSES = {r"\.xml(\.gz)?$", "/w/images/"}


def test_every_url_varnish_passes_is_dynamic_at_the_edge_or_static():
    """A URL Varnish never caches costs a PHP worker on every request, so the
    edge must put it in the dynamic class. When default.vcl gains a pass
    rule, the edge needs a matching acl (or the path is static)."""
    with open(VCL) as f:
        vcl = f.read()
    recv = vcl.split("sub vcl_recv", 1)[1].split("\nsub ", 1)[0]
    patterns = re.findall(
        r'if \(req\.url ~ "([^"]+)"\) \{\s*return ?\(pass\);', recv)
    assert patterns, "no pass rules found; has default.vcl changed shape?"
    cfg = edge.render([_inst()])["files"]["haproxy.cfg"]
    for pattern in patterns:
        if pattern in STATIC_PASSES:
            continue
        needle = {
            "/w/api.php": "path_sub /api.php",
            "/w/rest.php/": "path_sub /rest.php/",
        }.get(pattern)
        if needle is None and "Special:" in pattern:
            needle = "url_sub -i Special:"
        assert needle and needle in cfg, "no edge rule for %s" % pattern
    assert "req.http.Cookie ~" in recv and "([sS]ession|Token)=" in cfg
    assert "req.http.Authorization" in recv and "req.hdr(authorization)" in cfg
    assert 'req.method != "GET"' in recv and "!METH_GET" in cfg


def test_a_backend_with_no_running_server_answers_503_at_once():
    cfg = edge.render([_inst()])["files"]["haproxy.cfg"]
    for kind in ("dynamic", "default"):
        section = cfg.split("backend be_wiki1_%s\n" % kind, 1)[1].split("\n\n", 1)[0]
        assert section.startswith("    http-request return status 503"), kind
        assert "if { nbsrv eq 0 }" in section
