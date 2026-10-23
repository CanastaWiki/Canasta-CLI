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
              {"TLS_MODE": "custom", "PM_MAX_CHILDREN": "10",
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
    with pytest.raises(edge.EdgeError, match="TLS_MODE is 'acmee'"):
        _inst(env={"TLS_MODE": "acmee"})


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
        lines = section.splitlines()
        nbsrv = next(i for i, l in enumerate(lines) if "if { nbsrv eq 0 }" in l)
        server = next(i for i, l in enumerate(lines) if l.strip().startswith("server "))
        assert lines[nbsrv].startswith("    http-request return status 503"), kind
        assert nbsrv < server


def test_acme_dns_needs_a_supported_provider():
    with pytest.raises(edge.EdgeError, match="ACME_DNS_PROVIDER is 'route53'"):
        _inst(env={"TLS_MODE": "acme-dns", "ACME_DNS_PROVIDER": "route53",
                   "CLOUDFLARE_API_TOKEN": "t"})


def test_acme_dns_certificates_are_per_hostname_and_carry_no_token():
    inst = _inst(urls=("example.com", "www.example.com"),
                 env={"TLS_MODE": "acme-dns", "CLOUDFLARE_API_TOKEN": "t0k"})
    rendered = edge.render([inst])
    assert [c["file"] for c in rendered["certificates"]] == [
        "example.com.pem", "www.example.com.pem"]
    assert not any("t0k" in content for content in rendered["files"].values())


def test_the_dns_credentials_are_per_host_under_gitops():
    import yaml
    with open(os.path.join(REPO_ROOT, "vars", "secret_classification.yml")) as f:
        cls = yaml.safe_load(f)
    for key in ("ACME_DNS_PROVIDER", "CLOUDFLARE_ZONE_ID"):
        assert key in cls["canasta_host_specific_nonsecret"]
    assert re.search(cls["canasta_secret_key_pattern"], "CLOUDFLARE_API_TOKEN")


# --- mitigation ----------------------------------------------------------------- #

def test_rate_limits_follow_the_instance_settings():
    inst = _inst(env={"CANASTA_EDGE_RATE_DYNAMIC": "10", "CANASTA_EDGE_RATE_DEFAULT": "0"})
    cfg = edge.render([inst])["files"]["haproxy.cfg"]
    dynamic = cfg.split("backend be_wiki1_dynamic\n", 1)[1].split("\n\n", 1)[0]
    default = cfg.split("backend be_wiki1_default\n", 1)[1].split("\n\n", 1)[0]
    assert "sc_gpc_rate(0,0) gt 10" in dynamic
    assert "sc_gpc_rate(0,0)" not in default
    assert "sc_gpc_rate(1,0) gt %d" % edge.DEFAULT_ERROR_RATE in default


def test_maxconn_can_be_overridden():
    inst = _inst(env={"CANASTA_EDGE_DYNAMIC_MAXCONN": "4", "CANASTA_EDGE_DEFAULT_MAXCONN": "40"})
    assert edge.backend_limits(inst) == (4, 40)


@pytest.mark.parametrize("key,value", [
    ("CANASTA_EDGE_RATE_DYNAMIC", "-1"), ("CANASTA_EDGE_RATE_DEFAULT", "lots"),
    ("CANASTA_EDGE_DYNAMIC_MAXCONN", "0"),
])
def test_bad_limits_are_refused(key, value):
    with pytest.raises(edge.EdgeError, match=key):
        _inst(env={key: value})


def test_a_bad_deny_range_names_its_line():
    with pytest.raises(edge.EdgeError, match="edge-deny-cidrs.list line 3, '10.0.0.300'"):
        edge.instance_from_config("one", {}, [{"id": "w", "url": "example.com"}],
                                  deny_cidrs="# ok\n192.0.2.0/24\n10.0.0.300\n")


def test_a_trusted_cidr_list_trusts_the_last_forwarded_address():
    inst = _inst(env={"TRUSTED_PROXIES": "10.1.0.0/16, 192.0.2.4"})
    rendered = edge.render([inst])
    assert rendered["files"]["trusted-wiki1.lst"] == "10.1.0.0/16\n192.0.2.4/32\n"
    assert "req.hdr_ip(X-Forwarded-For,-1)" in rendered["files"]["haproxy.cfg"]


def test_a_cdn_needs_its_ranges():
    inst = _inst(env={"TRUSTED_PROXIES": "imperva"})
    with pytest.raises(edge.EdgeError, match="No address ranges for imperva"):
        edge.render([inst])
    cfg = edge.render([inst], cdn_ranges={"imperva": ["192.0.2.0/24"]})["files"]
    assert "req.hdr_ip(Incap-Client-IP) if { var(txn.inst) -m str wiki1 }" in cfg["haproxy.cfg"]
    assert cfg["cdn-imperva.lst"] == "192.0.2.0/24\n"


@pytest.mark.parametrize("value,header", [
    ("imperva@203.0.113.10/32, 203.0.113.11", "Incap-Client-IP"),
    ("Cloudflare@203.0.113.10/32,203.0.113.11/32", "CF-Connecting-IP"),
])
def test_a_cdn_behind_listed_proxies_is_trusted_from_those_proxies(value, header):
    inst = _inst(env={"TRUSTED_PROXIES": value})
    assert (inst["trusted_proxies"], inst["trusted_header"]) == ("cidrs", header)
    files = edge.render([inst])["files"]
    assert files["trusted-wiki1.lst"] == "203.0.113.10/32\n203.0.113.11/32\n"
    assert not [f for f in files if f.startswith("cdn-")]
    rules = [line for line in files["haproxy.cfg"].splitlines() if "set-src" in line]
    assert rules == ["    http-request set-src req.hdr_ip(%s) if { var(txn.inst) -m str wiki1 } "
                     "{ src -f /usr/local/etc/haproxy/trusted-wiki1.lst }" % header]


@pytest.mark.parametrize("value", [
    "akamai@203.0.113.10/32", "imperva@", "imperva@ , ", "@203.0.113.10/32",
])
def test_a_bad_cdn_behind_proxies_form_is_refused(value):
    with pytest.raises(edge.EdgeError, match="<cdn>@<CIDRs>"):
        _inst(env={"TRUSTED_PROXIES": value})


def test_a_bad_proxy_address_in_the_cdn_form_names_it():
    with pytest.raises(edge.EdgeError, match="'203.0.113.300'"):
        _inst(env={"TRUSTED_PROXIES": "imperva@203.0.113.300"})


@pytest.mark.parametrize("value,rule,lists", [
    ("cloudflare", "req.hdr_ip(CF-Connecting-IP) if { var(txn.inst) -m str wiki1 } "
     "{ src -f /usr/local/etc/haproxy/cdn-cloudflare.lst }", {"cdn-cloudflare.lst"}),
    ("imperva", "req.hdr_ip(Incap-Client-IP) if { var(txn.inst) -m str wiki1 } "
     "{ src -f /usr/local/etc/haproxy/cdn-imperva.lst }", {"cdn-imperva.lst"}),
    ("203.0.113.0/24", "req.hdr_ip(X-Forwarded-For,-1) if { var(txn.inst) -m str wiki1 } "
     "{ src -f /usr/local/etc/haproxy/trusted-wiki1.lst }", {"trusted-wiki1.lst"}),
])
def test_the_existing_trusted_proxy_forms_render_as_before(value, rule, lists):
    inst = _inst(env={"TRUSTED_PROXIES": value})
    assert inst["trusted_header"] is None
    files = edge.render([inst], cdn_ranges={"cloudflare": ["192.0.2.0/24"],
                                            "imperva": ["192.0.2.0/24"]})["files"]
    rules = [line for line in files["haproxy.cfg"].splitlines() if "set-src" in line]
    assert rules == ["    http-request set-src " + rule]
    assert {f for f in files if f.endswith(".lst")} == lists


def test_uploads_skip_the_body_wait():
    cfg = edge.render([_inst()])["files"]["haproxy.cfg"]
    assert "http-request wait-for-body time 5s at-least 2m if !upload" in cfg


def test_extra_hostnames_are_routed_once():
    inst = edge.instance_from_config(
        "one", {}, [{"id": "w", "url": "example.com"}],
        extra_hostnames=["www.example.com", "example.com"])
    assert inst["hostnames"] == ["example.com", "www.example.com"]


def test_media_hosts_get_their_own_unthrottled_backend():
    inst = _inst(media=("images.example.com",), env={"CANASTA_EDGE_MEDIA_MAXCONN": "50"})
    cfg = edge.render([inst])["files"]["haproxy.cfg"]
    media = cfg.split("backend be_wiki1_media\n", 1)[1].split("\n\n", 1)[0]
    assert "track-sc0" not in media and "maxconn 50" in media
    assert "use_backend be_%[var(txn.inst)]_media if known_site media_host" in cfg


def test_no_media_backend_without_media_hosts():
    assert "be_wiki1_media" not in edge.render([_inst()])["files"]["haproxy.cfg"]


def test_ipv6_adds_ipv6_listeners():
    plain = edge.render(_two(), acme_thumbprint="THUMB")["files"]["haproxy.cfg"]
    assert ":::" not in plain
    cfg = edge.render(_two(), acme_thumbprint="THUMB", ipv6=True)["files"]["haproxy.cfg"]
    assert "    bind :::80 v6only\n" in cfg
    assert "    bind :::443 v6only ssl crt-list" in cfg


def test_the_shipped_snippet_template_adds_nothing():
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(root, "instance_template", "config", "haproxy-backend.cfg")) as f:
        template = f.read()
    assert _inst(snippet=template)["snippet"] == []


def test_the_spoe_message_is_one_the_bouncer_answers():
    # crowdsecurity/spoa-bouncer ignores any other message name.
    inst = _inst(env={"CANASTA_ENABLE_CROWDSEC": "true", "CROWDSEC_SPOA_API_KEY": "k"})
    spoe = edge.render([inst])["files"]["spoe-wiki1.cfg"]
    assert "spoe-message crowdsec-http-no-body\n" in spoe
    assert "messages crowdsec-http-no-body\n" in spoe
    assert "src-ip=src " in spoe


# --- ports and plain HTTP ------------------------------------------------------ #

def _cfg(instances):
    return edge.render(instances, acme_thumbprint="T")


def test_an_instance_on_its_own_ports_gets_its_own_frontends():
    r = _cfg([_inst("one", ("other.example.org:8443",), {"HTTP_PORT": "8080", "HTTPS_PORT": "8443"})])
    cfg = r["files"]["haproxy.cfg"]
    assert r["ports"] == [8080, 8443]
    assert "frontend http_8080\n" in cfg and "frontend https_8443\n" in cfg
    assert "frontend http\n" not in cfg
    assert ("http-request redirect location https://%[req.hdr(host),field(1,:)]:8443%[pathq] "
            "code 301 if { var(txn.inst) -m str one }") in cfg
    assert "other.example.org@8443 one/wiki" in r["files"]["hosts.map"]


def test_a_url_port_must_match_the_instance_port():
    with pytest.raises(edge.EdgeError, match="has port 9443, but HTTPS_PORT is 8443"):
        _inst("one", ("example.com:9443",), {"HTTPS_PORT": "8443"})


def test_a_plain_http_instance_is_routed_without_https():
    plain = _inst("plain", ("plain.example.net",),
                  {"TLS_MODE": "off", "TRUSTED_PROXIES": "10.0.0.0/8"})
    secure = _inst("secure", ("example.com",))
    r = _cfg([plain, secure])
    cfg = r["files"]["haproxy.cfg"]
    http = cfg[cfg.index("frontend http\n"):cfg.index("frontend https\n")]
    assert "use_backend be_%[var(txn.inst)]_default if known_site" in http
    assert "redirect scheme https code 301 if { var(txn.inst) -m str secure }" in http
    assert ("del-header X-Forwarded-Proto if { var(txn.inst) -m str plain } "
            "!{ src -f /usr/local/etc/haproxy/trusted-plain.lst }") in http
    assert not [c for c in r["certificates"] if c["instance"] == "plain"]
    assert "plain.example.net@443" not in r["files"]["hosts.map"]


def test_plain_http_without_a_trusted_proxy_has_no_per_client_limits():
    plain = _inst("plain", ("plain.example.net",), {"TLS_MODE": "off"})
    cfg = _cfg([plain])["files"]["haproxy.cfg"]
    assert "track-sc0" not in cfg
    assert "del-header X-Forwarded-Proto if { var(txn.inst) -m str plain }\n" in cfg


def test_a_localhost_instance_gets_its_ports_unmatched_traffic():
    r = _cfg([_inst("local", ("localhost:8444",), {"HTTP_PORT": "8081", "HTTPS_PORT": "8444"})])
    assert "@8444 local/wiki" in r["files"]["hosts.map"]
    assert "@8081 local/wiki" in r["files"]["hosts.map"]
    assert not r["certificates"]


def test_two_unnamed_instances_cannot_share_a_port():
    with pytest.raises(edge.EdgeError, match="cannot tell apart"):
        _cfg([_inst("a", ("localhost",)), _inst("b", ("192.0.2.4",))])


def test_a_port_cannot_be_both_http_and_https():
    with pytest.raises(edge.EdgeError, match="both an HTTP port and an HTTPS port"):
        _cfg([_inst("a", ("a.example.com:8443",), {"HTTPS_PORT": "8443"}),
              _inst("b", ("b.example.com:8443",), {"TLS_MODE": "off", "HTTP_PORT": "8443"})])


def test_the_same_hostname_may_be_served_on_different_ports():
    r = _cfg([_inst("a", ("example.com",)),
              _inst("b", ("example.com:8443",), {"HTTP_PORT": "8080", "HTTPS_PORT": "8443"})])
    hosts = r["files"]["hosts.map"]
    assert "example.com@443 a/wiki" in hosts and "example.com@8443 b/wiki" in hosts


def test_listeners_follow_the_ports():
    assert edge.listeners_for([443, 80, 8443], ipv6=True, sockets=True) == {
        80: ["fd@3", "fd@6"], 443: ["fd@4", "fd@7"], 8443: ["fd@5", "fd@8"]}
    assert edge.listeners_for([80], ipv6=False) == {80: [":80"]}


def test_rootless_docker_has_no_per_client_limits_unless_set():
    cfg = edge.render([_inst("one")], rootless=True)["files"]["haproxy.cfg"]
    assert "track-sc0" not in cfg
    rated = _inst("one", env={"CANASTA_EDGE_RATE_DYNAMIC": "120"})
    assert "track-sc0" in edge.render([rated], rootless=True)["files"]["haproxy.cfg"]
    assert "track-sc0" in edge.render([_inst("one")])["files"]["haproxy.cfg"]


def test_private_addresses_are_exempt_from_per_client_limits():
    cfg = edge.render([_inst("one", ["one.example.com"])])["files"]["haproxy.cfg"]
    assert ("http-request track-sc0 src if !{ src 10.0.0.0/8 172.16.0.0/12 "
            "192.168.0.0/16 127.0.0.0/8 ::1 fc00::/7 }") in cfg


def test_rootless_docker_limits_apply_to_every_address():
    inst = _inst("one", ["one.example.com"], env={"CANASTA_EDGE_RATE_DYNAMIC": "60"})
    cfg = edge.render([inst], rootless=True)["files"]["haproxy.cfg"]
    assert "    http-request track-sc0 src\n" in cfg
