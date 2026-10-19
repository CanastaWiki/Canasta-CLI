# -*- coding: utf-8 -*-
"""Render the HAProxy edge configuration for the Compose instances on a host.

Pure functions: each instance's parsed settings go in, the edge's files come
out. Nothing here reads the instance directories or talks to Docker.

The edge terminates TLS on 80/443 and routes by hostname to each instance's
Caddy, reached on the shared edge network as caddy-<id>. Every instance gets
two backends pointing at that Caddy:

  dynamic  requests that always reach PHP: the requests Varnish never serves
           from cache, plus Special: pages, non-view actions and old
           revisions. Capped below PM_MAX_CHILDREN so they queue here
           rather than exhausting the PHP pool.
  default  everything else. Cache hits and static files must not wait behind
           PHP, so this cap only guards against a flood.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import ipaddress
import math
import re

HAPROXY_IMAGE = "docker.io/library/haproxy:3.2.25-alpine"
# Also the edge's openssl: the HAProxy image has none, and hosts may not.
ACME_IMAGE = "docker.io/neilpang/acme.sh:3.1.6"
# CrowdSec's HAProxy bouncer; one per CrowdSec-enabled instance behind the
# edge, run in the instance's own stack.
SPOA_IMAGE = "docker.io/crowdsecurity/spoa-bouncer:v0.3.1"

CONFIG_DIR = "/usr/local/etc/haproxy"
CERT_DIR = CONFIG_DIR + "/certs"
DEFAULT_CERT = CERT_DIR + "/default.pem"
STATS_SOCKET = "/var/run/haproxy/admin.sock"

TLS_MODES = ("acme", "acme-dns", "internal", "custom", "off")
# DNS providers acme-dns supports, as acme.sh names their hooks.
DNS_PROVIDERS = {"cloudflare": "dns_cf"}

DEFAULT_PM_MAX_CHILDREN = 25
DYNAMIC_SHARE = 0.6
DEFAULT_FLOOD_FACTOR = 8
# Without Varnish every page view reaches PHP.
DEFAULT_FLOOD_FACTOR_NO_VARNISH = 2
# Idle keep-alive connections count against the process limit as well.
GLOBAL_MAXCONN_FACTOR = 4
GLOBAL_MAXCONN_FLOOR = 2000
# Media hostnames proxy object storage and never reach PHP, so their cap is
# not tied to PM_MAX_CHILDREN.
DEFAULT_MEDIA_MAXCONN = 500

# Requests one client may make per RATE_WINDOW to each backend class before
# getting 429, and the error responses it may draw (scanners probing for
# paths draw a stream of 404s). Overridable per instance; 0 turns a limit off.
RATE_WINDOW = "10s"
DEFAULT_RATE_DYNAMIC = 60
DEFAULT_RATE_DEFAULT = 300
DEFAULT_ERROR_RATE = 50
# Source addresses no per-client rate limit applies to.
PRIVATE_SOURCES = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
                   "::1", "fc00::/7")
# How long a request body may take to arrive before the edge gives up, and
# how much of it is buffered: a slow-sent body would otherwise hold a
# backend connection. Uploads are exempt.
BODY_WAIT = "5s"
BODY_BUFFER = "2m"

# CDNs whose client-address header the edge trusts, from their own address
# ranges only. TRUSTED_PROXIES names one, or lists CIDRs to trust for
# the last X-Forwarded-For entry.
CDN_HEADERS = {"cloudflare": "CF-Connecting-IP", "imperva": "Incap-Client-IP"}

# The edge's .env settings an instance can override.
LIMIT_KEYS = {
    "dynamic_maxconn": "CANASTA_EDGE_DYNAMIC_MAXCONN",
    "default_maxconn": "CANASTA_EDGE_DEFAULT_MAXCONN",
    "media_maxconn": "CANASTA_EDGE_MEDIA_MAXCONN",
    "rate_dynamic": "CANASTA_EDGE_RATE_DYNAMIC",
    "rate_default": "CANASTA_EDGE_RATE_DEFAULT",
}

DENY_UA_FILE = "config/edge-deny-user-agents.list"
DENY_CIDR_FILE = "config/edge-deny-cidrs.list"

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_INSTANCE_ID = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-_]*[a-zA-Z0-9])?$")

# Lines of an instance snippet are inlined into its backends. A line that
# opens a section would end the backend early and attach the rest of the
# snippet, and every generated line after it, to that section.
_SECTION_KEYWORDS = (
    "backend", "cache", "crt-store", "defaults", "frontend", "global",
    "http-errors", "listen", "log-forward", "mailers", "peers", "program",
    "resolvers", "ring", "traces", "userlist",
)


class EdgeError(Exception):
    """An instance's settings cannot be served by the edge."""


def _truthy(value, default):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() == "true"


def _hostname(name, instance_id, where):
    """Return name lowercased, or raise if the edge cannot route it."""
    host = (name or "").strip().lower().rstrip(".")
    problem = None
    if not host:
        problem = "is empty"
    elif ":" in host:
        problem = "has a port; the edge serves every site on 443"
    elif host == "localhost" or host.endswith(".localhost"):
        problem = "is localhost, which cannot be routed by hostname"
    elif _IPV4.match(host):
        problem = "is an IP address, which cannot be routed by hostname"
    elif "*" in host:
        problem = "is a wildcard"
    elif len(host) > 253 or not all(_LABEL.match(p) for p in host.split(".")):
        problem = "is not a valid hostname"
    if problem:
        raise EdgeError("Instance '%s': %s '%s' %s." % (
            instance_id, where, name, problem))
    return host


def _snippet_lines(snippet, instance_id):
    lines = []
    for raw in (snippet or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.split()[0].lower() in _SECTION_KEYWORDS:
            raise EdgeError(
                "Instance '%s': the edge snippet line '%s' opens a new "
                "section. The snippet is inlined into this instance's "
                "backends and may hold only backend directives."
                % (instance_id, line))
        lines.append(line)
    return lines


def _list_lines(text):
    """(line number, entry) for the non-blank, non-comment lines."""
    for number, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield number, line


def _networks(text, instance_id, where):
    """CIDRs from a list, normalized; raises naming the line of a bad one."""
    nets = []
    for number, line in _list_lines(text):
        try:
            nets.append(str(ipaddress.ip_network(line, strict=False)))
        except ValueError:
            raise EdgeError("Instance '%s': %s line %d, '%s', is not an IP "
                            "address or CIDR range." % (instance_id, where, number, line))
    return nets


def _user_agents(text, instance_id):
    agents = []
    for number, line in _list_lines(text):
        if any(ord(c) < 32 for c in line):
            raise EdgeError("Instance '%s': %s line %d holds a control "
                            "character." % (instance_id, DENY_UA_FILE, number))
        agents.append(line)
    return agents


def rates_set(env):
    """Whether the operator set the per-client rates, which then apply even
    where client addresses may be lost (rootless Docker)."""
    return any((env.get(LIMIT_KEYS[k]) or "").strip()
               for k in ("rate_dynamic", "rate_default"))


def _limit(env, key, instance_id, default, allow_zero):
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < (0 if allow_zero else 1):
        raise EdgeError("Instance '%s': %s is '%s', not a %s number."
                        % (instance_id, key, raw,
                           "non-negative" if allow_zero else "positive"))
    return value


def _wiki_server(server, instance_id, where):
    """(host, port, unnamed) for a wiki URL's host part. unnamed is True for
    localhost and IP addresses, which the edge cannot route by name; it
    sends them its port's unmatched traffic instead."""
    host, _, port = (server or "").strip().lower().rstrip(".").partition(":")
    if port and not port.isdigit():
        raise EdgeError("Instance '%s': %s '%s' has an invalid port."
                        % (instance_id, where, server))
    port = int(port) if port else None
    if host == "localhost" or host.endswith(".localhost") or _IPV4.match(host):
        return host, port, True
    return _hostname(host, instance_id, where), port, False


def _port(env, key, instance_id, default):
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise EdgeError("Instance '%s': %s is '%s', not a port number."
                        % (instance_id, key, raw))
    return int(raw)


def instance_from_config(instance_id, env, wikis, media_hosts=(), snippet="",
                         deny_user_agents="", deny_cidrs="", extra_hostnames=()):
    """Build the edge's view of one instance.

    env is the instance's .env as a dict, wikis the parsed wikis.yaml list,
    media_hosts the parsed media-hosts.yaml list, snippet the text of its
    edge snippet, and deny_user_agents and deny_cidrs the text of its deny
    lists (each empty when it has none). extra_hostnames are names its own
    Caddy serves besides its wikis, from site blocks in Caddyfile.global.
    """
    if not _INSTANCE_ID.match(instance_id or ""):
        raise EdgeError("'%s' is not a valid instance ID." % instance_id)

    # Served over plain HTTP, behind the operator's own TLS proxy or with
    # --skip-tls: no HTTPS, no certificates, no redirect.
    tls_mode = (env.get("TLS_MODE") or "acme").strip().lower()
    if tls_mode not in TLS_MODES:
        raise EdgeError(
            "Instance '%s': TLS_MODE is '%s'. Valid values are %s."
            % (instance_id, tls_mode, ", ".join("'%s'" % m for m in TLS_MODES)))
    plain = tls_mode == "off"
    http_port = _port(env, "HTTP_PORT", instance_id, 80)
    https_port = _port(env, "HTTPS_PORT", instance_id, 443)
    if not plain and http_port == https_port:
        raise EdgeError("Instance '%s': HTTP_PORT and HTTPS_PORT are both %d."
                        % (instance_id, http_port))
    served_port, served_key = (http_port, "HTTP_PORT") if plain else (https_port, "HTTPS_PORT")

    hostnames = []
    unnamed = []
    for wiki in wikis or []:
        server = str(wiki.get("url", "")).split("/", 1)[0]
        where = "wiki '%s' URL host" % wiki.get("id", "?")
        host, port, is_unnamed = _wiki_server(server, instance_id, where)
        if port is not None and port != served_port:
            raise EdgeError(
                "Instance '%s': %s '%s' has port %d, but %s is %d."
                % (instance_id, where, server, port, served_key, served_port))
        names = unnamed if is_unnamed else hostnames
        if host not in names:
            names.append(host)
    if not hostnames and not unnamed:
        raise EdgeError("Instance '%s' has no wikis." % instance_id)

    media = []
    for entry in media_hosts or []:
        host = _hostname(entry.get("hostname"), instance_id, "media host")
        if host in hostnames or host in media:
            raise EdgeError(
                "Instance '%s' declares '%s' more than once." % (instance_id, host))
        media.append(host)

    for name in extra_hostnames or ():
        host = _hostname(name, instance_id, "Caddyfile.global site")
        if host not in hostnames and host not in media:
            hostnames.append(host)

    dns = None
    if tls_mode == "acme-dns":
        provider = (env.get("ACME_DNS_PROVIDER") or "cloudflare").strip().lower()
        if provider not in DNS_PROVIDERS:
            raise EdgeError(
                "Instance '%s': ACME_DNS_PROVIDER is '%s'. Supported: %s."
                % (instance_id, provider, ", ".join(sorted(DNS_PROVIDERS))))
        token = (env.get("CLOUDFLARE_API_TOKEN") or "").strip()
        if not token:
            raise EdgeError(
                "Instance '%s': TLS_MODE is 'acme-dns' but "
                "CLOUDFLARE_API_TOKEN is not set. Set it to a Cloudflare API "
                "token that can edit the DNS of the instance's zone."
                % instance_id)
        dns = {"provider": provider, "hook": DNS_PROVIDERS[provider],
               "token": token,
               "zone_id": (env.get("CLOUDFLARE_ZONE_ID") or "").strip()}

    raw_children = (env.get("PM_MAX_CHILDREN") or "").strip()
    try:
        pm_max_children = int(raw_children) if raw_children else DEFAULT_PM_MAX_CHILDREN
    except ValueError:
        pm_max_children = 0
    if pm_max_children < 1:
        raise EdgeError(
            "Instance '%s': PM_MAX_CHILDREN is '%s', not a positive number."
            % (instance_id, raw_children))

    trusted = (env.get("TRUSTED_PROXIES") or "").strip()
    if trusted.lower() in CDN_HEADERS:
        trusted_proxies, trusted_cidrs = trusted.lower(), []
    elif trusted:
        trusted_proxies = "cidrs"
        trusted_cidrs = _networks(trusted.replace(",", "\n"), instance_id,
                                  "TRUSTED_PROXIES entry")
    else:
        trusted_proxies, trusted_cidrs = None, []

    return {
        "id": instance_id,
        "hostnames": hostnames,
        # localhost or IP addresses, reached through its ports' unmatched
        # traffic.
        "unnamed": bool(unnamed),
        "plain": plain,
        "http_port": http_port,
        "https_port": https_port,
        "media_hostnames": media,
        "tls_mode": tls_mode,
        "dns": dns,
        "staging_certs": _truthy(env.get("CANASTA_STAGING_CERTS"), False),
        "tls_cert": (env.get("TLS_CERT") or "origin.crt").strip(),
        "tls_key": (env.get("TLS_KEY") or "origin.key").strip(),
        "pm_max_children": pm_max_children,
        "varnish": _truthy(env.get("CANASTA_ENABLE_VARNISH"), True),
        "snippet": _snippet_lines(snippet, instance_id),
        "trusted_proxies": trusted_proxies,
        "trusted_cidrs": trusted_cidrs,
        "crowdsec": crowdsec_enforced(env),
        "rates_set": rates_set(env),
        "deny_user_agents": _user_agents(deny_user_agents, instance_id),
        "deny_cidrs": _networks(deny_cidrs, instance_id, DENY_CIDR_FILE),
        "limits": {
            "dynamic_maxconn": _limit(env, LIMIT_KEYS["dynamic_maxconn"], instance_id, None, False),
            "default_maxconn": _limit(env, LIMIT_KEYS["default_maxconn"], instance_id, None, False),
            "media_maxconn": _limit(env, LIMIT_KEYS["media_maxconn"], instance_id,
                                    DEFAULT_MEDIA_MAXCONN, False),
            "rate_dynamic": _limit(env, LIMIT_KEYS["rate_dynamic"], instance_id,
                                   DEFAULT_RATE_DYNAMIC, True),
            "rate_default": _limit(env, LIMIT_KEYS["rate_default"], instance_id,
                                   DEFAULT_RATE_DEFAULT, True),
        },
    }


def backend_limits(instance):
    """Per-server maxconn for the instance's (dynamic, default) backends."""
    children = instance["pm_max_children"]
    dynamic = max(1, int(math.ceil(children * DYNAMIC_SHARE)))
    factor = (DEFAULT_FLOOD_FACTOR if instance["varnish"]
              else DEFAULT_FLOOD_FACTOR_NO_VARNISH)
    overrides = instance.get("limits") or {}
    return (overrides.get("dynamic_maxconn") or dynamic,
            overrides.get("default_maxconn") or children * factor)


def certificates(instances):
    """The certificate files the edge serves, one entry per crt-list line.

    acme, acme-dns and internal certificates are one file per hostname. A custom
    certificate is the instance's own, covering all its hostnames.
    """
    entries = []
    for inst in instances:
        names = inst["hostnames"] + inst["media_hostnames"]
        # Plain HTTP instances are not served over HTTPS; unnamed ones get
        # the edge's default certificate.
        if inst["plain"] or not names:
            continue
        if inst["tls_mode"] == "custom":
            entries.append({
                "instance": inst["id"], "mode": "custom",
                "file": "custom-%s.pem" % inst["id"], "hostnames": names,
                "cert": inst["tls_cert"], "key": inst["tls_key"],
            })
            continue
        for host in names:
            entries.append({
                "instance": inst["id"], "mode": inst["tls_mode"],
                "file": "%s.pem" % host, "hostnames": [host],
                "staging": inst["staging_certs"],
            })
    return entries


def _check_unique(instances):
    ids = {}
    owners = {}
    for inst in instances:
        if inst["id"] in ids:
            raise EdgeError("Instance ID '%s' appears twice." % inst["id"])
        ids[inst["id"]] = True
        for port in _ports_of(inst):
            for host in inst["hostnames"] + inst["media_hostnames"]:
                if (host, port) in owners:
                    raise EdgeError(
                        "Instances '%s' and '%s' both serve %s on port %d. The "
                        "edge routes each hostname on a port to one instance."
                        % (owners[(host, port)], inst["id"], host, port))
                owners[(host, port)] = inst["id"]
            if inst["unnamed"]:
                if ("", port) in owners:
                    raise EdgeError(
                        "Instances '%s' and '%s' are both reached by localhost or "
                        "an IP address on port %d, which the edge cannot tell "
                        "apart. Give one of them its own HTTP_PORT and HTTPS_PORT."
                        % (owners[("", port)], inst["id"], port))
                owners[("", port)] = inst["id"]


def check_together(instances):
    """Raise EdgeError if the instances cannot share one edge."""
    _check_unique(instances)
    frontends(instances)


def _ports_of(inst):
    """The ports the edge serves the instance on."""
    return [inst["http_port"]] if inst["plain"] else [inst["http_port"], inst["https_port"]]


def frontends(instances):
    """[(port, tls)] for every port the edge listens on, in port order. A
    port carries either plain HTTP (redirects, ACME challenges and plain
    HTTP instances) or TLS, never both."""
    roles = {}
    for inst in instances:
        for port, tls in ([(inst["http_port"], False)] if inst["plain"]
                          else [(inst["http_port"], False), (inst["https_port"], True)]):
            if roles.setdefault(port, tls) != tls:
                raise EdgeError(
                    "Port %d is both an HTTP port and an HTTPS port of the edge's "
                    "instances; give them different HTTP_PORT and HTTPS_PORT values."
                    % port)
    return sorted(roles.items())


def _check_dns_tokens(instances):
    """acme.sh keeps a Cloudflare token with the certificates only when a
    zone ID comes with it; otherwise one token is saved for the whole edge,
    and renewals for every instance would use whichever was saved last."""
    tokens = {}
    for inst in instances:
        dns = inst.get("dns")
        if dns and not dns["zone_id"]:
            tokens.setdefault(dns["token"], []).append(inst["id"])
    if len(tokens) > 1:
        names = sorted(i for ids in tokens.values() for i in ids)
        raise EdgeError(
            "Instances %s use acme-dns with different Cloudflare tokens. Set "
            "CLOUDFLARE_ZONE_ID for each, so the edge keeps every token with "
            "its own certificates." % ", ".join("'%s'" % n for n in names))


def _acme_rule(thumbprint):
    if not thumbprint:
        return []
    return [
        "    http-request return status 200 content-type text/plain "
        "lf-string \"%%[path,field(-1,/)].%s\\n\" "
        "if { path_beg /.well-known/acme-challenge/ }" % thumbprint,
    ]


def _list_path(name):
    return "%s/%s" % (CONFIG_DIR, name)


def _client_address_rules(instances):
    """Behind a CDN the edge's peer is the CDN, so per-client limits would
    throttle the CDN itself. For an instance that names its CDN, take the
    client address from the CDN's header, on requests from the CDN's own
    ranges only."""
    out = []
    for inst in instances:
        mode = inst["trusted_proxies"]
        if not mode:
            continue
        own = "{ var(txn.inst) -m str %s }" % inst["id"]
        if mode == "cidrs":
            out.append("    http-request set-src req.hdr_ip(X-Forwarded-For,-1) "
                       "if %s { src -f %s }"
                       % (own, _list_path("trusted-%s.lst" % inst["id"])))
        else:
            out.append("    http-request set-src req.hdr_ip(%s) if %s { src -f %s }"
                       % (CDN_HEADERS[mode], own, _list_path("cdn-%s.lst" % mode)))
    return out


def crowdsec_enforced(env):
    """Whether the edge enforces the instance's CrowdSec decisions: CrowdSec
    is on and the instance's edge bouncer is enrolled."""
    return (_truthy(env.get("CANASTA_ENABLE_CROWDSEC"), False)
            and bool((env.get("CROWDSEC_SPOA_API_KEY") or "").strip()))


def _crowdsec_rules(inst):
    """Ask the instance's CrowdSec bouncer about the client, and refuse a
    banned one before it counts against any limit. When the bouncer cannot
    answer, no remediation is set and the request goes on (fail open)."""
    if not inst["crowdsec"]:
        return []
    engine = "crowdsec-%s" % inst["id"]
    return [
        "    filter spoe engine %s config %s" % (engine, _list_path("spoe-%s.cfg" % inst["id"])),
        "    http-request send-spoe-group %s crowdsec-http" % engine,
        "    http-request deny deny_status 403 if { var(txn.crowdsec.remediation) -m str ban }",
    ]


def _render_spoe(inst):
    return "\n".join([
        "# Generated by Canasta. Do not edit: changes are lost on the next render.",
        "[crowdsec-%s]" % inst["id"],
        "spoe-agent crowdsec-agent",
        "    groups crowdsec-http",
        "    option var-prefix crowdsec",
        "    option set-on-error error",
        "    timeout hello 200ms",
        "    timeout idle 55s",
        "    timeout processing 500ms",
        "    use-backend be_%s_crowdsec" % inst["id"],
        "",
        # The client address is the one the frontend recovered. The bouncer
        # answers only the message names it knows, and ignores others.
        "spoe-message crowdsec-http-no-body",
        "    args remediation=var(txn.crowdsec.remediation) method=method path=path "
        "query=query version=req.ver headers=req.hdrs url=url ssl=ssl_fc "
        "src-ip=src src-port=src_port",
        "",
        "spoe-group crowdsec-http",
        "    messages crowdsec-http-no-body",
        "",
    ])


def _backend_rules(inst, kind, rootless=False):
    """CrowdSec bans and the deny lists, then per-client rate limits, ahead
    of everything else. Media requests cost no PHP, so a page with many
    images is not throttled."""
    out = _crowdsec_rules(inst)
    if inst["deny_cidrs"]:
        out.append("    http-request deny deny_status 403 if { src -f %s }"
                   % _list_path("deny-cidr-%s.lst" % inst["id"]))
    if inst["deny_user_agents"]:
        out.append("    http-request deny deny_status 403 if "
                   "{ req.hdr(user-agent) -m sub -i -f %s }"
                   % _list_path("deny-ua-%s.lst" % inst["id"]))
    if kind == "media":
        return out
    # Behind the operator's own proxy with no trusted proxy set, every
    # request comes from that proxy, and on rootless Docker from its port
    # forwarder, so per-client limits would throttle all visitors together.
    if inst["plain"] and not inst["trusted_proxies"]:
        return out
    if rootless and not inst["rates_set"]:
        return out
    rate = inst["limits"]["rate_%s" % kind]
    # A client from the internet arrives with its public address; a private
    # one is the host itself (a wiki calling its own site through
    # gateway.docker.internal) or its local network. Under rootless Docker
    # every client looks private, so there the exemption would undo limits
    # the operator turned on.
    exempt = "" if rootless else " if !{ src %s }" % " ".join(PRIVATE_SOURCES)
    out += [
        "    stick-table type ipv6 size 100k expire 1m "
        "store http_req_rate(%s),http_err_rate(%s)" % (RATE_WINDOW, RATE_WINDOW),
        "    http-request track-sc0 src%s" % exempt,
    ]
    if rate:
        out.append("    http-request deny deny_status 429 if { sc0_http_req_rate gt %d }"
                   % rate)
    out.append("    http-request deny deny_status 429 if { sc0_http_err_rate gt %d }"
               % DEFAULT_ERROR_RATE)
    return out


def listeners_for(ports, ipv6=False, sockets=False):
    """{port: bind addresses} for the edge's ports. With sockets, HAProxy
    takes the listening sockets systemd passed it, in the order the edge's
    socket unit lists them: each port over IPv4, then each over IPv6."""
    ports = sorted(ports)
    if sockets:
        return {p: ["fd@%d" % (3 + i)] + (["fd@%d" % (3 + len(ports) + i)] if ipv6 else [])
                for i, p in enumerate(ports)}
    return {p: [":%d" % p] + ([":::%d v6only" % p] if ipv6 else []) for p in ports}


_ROUTING = [
    "    acl upload path_sub -i Special:Upload",
    "    acl upload url_param(action) -m str upload",
    "    http-request wait-for-body time %s at-least %s if !upload" % (BODY_WAIT, BODY_BUFFER),
    "    acl known_site var(txn.inst) -m found",
    "    acl media_host var(txn.site),field(2,/) -m str media",
    # The requests Varnish passes to MediaWiki on every request, plus
    # the expensive views it would cache but crawlers request by the
    # thousand. Images and sitemaps are passed too but are static files.
    "    acl dynamic path_sub /api.php",
    "    acl dynamic path_sub /rest.php/",
    "    acl dynamic url_sub -i Special:",
    "    acl dynamic url_sub -i Special%3A",
    "    acl dynamic url_param(diff) -m found",
    "    acl dynamic url_param(oldid) -m found",
    "    acl has_action url_param(action) -m found",
    "    acl view_action url_param(action) -m str view",
    # Varnish passes every request from a logged-in user, or from behind
    # basic auth, but the files a page pulls in cost no PHP: counted as
    # dynamic, one page of thumbnails would use up a client's allowance.
    "    acl credentials req.fhdr(cookie) -m reg ([sS]ession|Token)=",
    "    acl credentials req.hdr(authorization) -m found",
    "    acl static_file path_sub /w/images/ /w/resources/ /w/skins/ /w/extensions/",
    "    acl php path_end .php",
    "    use_backend be_%[var(txn.inst)]_media if known_site media_host",
    "    use_backend be_%[var(txn.inst)]_dynamic if known_site !media_host dynamic",
    "    use_backend be_%[var(txn.inst)]_dynamic if known_site !media_host credentials !static_file",
    "    use_backend be_%[var(txn.inst)]_dynamic if known_site !media_host credentials php",
    "    use_backend be_%[var(txn.inst)]_dynamic if known_site !media_host !METH_GET",
    "    use_backend be_%[var(txn.inst)]_dynamic if known_site !media_host has_action !view_action",
    "    use_backend be_%[var(txn.inst)]_default if known_site",
    "    default_backend be_no_site",
]


def _frontend_name(port, tls):
    base = "https" if tls else "http"
    return base if port == (443 if tls else 80) else "%s_%d" % (base, port)


def _site_lookup(port):
    """The site a request is for: its hostname on this port, else the
    port's unnamed instance."""
    return [
        "    http-request set-var(txn.port) str(%d)" % port,
        "    http-request set-var(txn.site) req.hdr(host),lower,field(1,:),"
        "concat(@,txn.port),map(%s/hosts.map)" % CONFIG_DIR,
        "    http-request set-var(txn.site) str(@),concat(,txn.port),map(%s/hosts.map) "
        "if !{ var(txn.site) -m found }" % CONFIG_DIR,
        "    http-request set-var(txn.inst) var(txn.site),field(1,/)",
    ]


def _render_frontend(port, tls, instances, thumbprint, binds):
    # option forwardfor in the frontends, not defaults: the CrowdSec
    # bouncers' backends speak TCP.
    out = ["frontend %s" % _frontend_name(port, tls), "    option forwardfor"]
    if tls:
        out += ["    bind %s ssl crt-list %s/crt-list alpn h2,http/1.1" % (b, CONFIG_DIR)
                for b in binds]
        out += ["    http-request set-header X-Forwarded-Proto https"]
        out += _acme_rule(thumbprint)
        out += _site_lookup(port)
        out += _client_address_rules(instances)
        return out + _ROUTING
    out += ["    bind %s" % b for b in binds]
    out += _acme_rule(thumbprint)
    plain = [i for i in instances if i["plain"] and i["http_port"] == port]
    secure = [i for i in instances if not i["plain"] and i["http_port"] == port]
    elsewhere = [i for i in secure if i["https_port"] != 443]
    if not plain and not elsewhere:
        return out + ["    http-request redirect scheme https code 301"]
    out += _site_lookup(port)
    # Only a proxy the instance trusts may say the client used HTTPS.
    for inst in plain:
        own = "{ var(txn.inst) -m str %s }" % inst["id"]
        trusted = _trusted_list(inst)
        out.append("    http-request del-header X-Forwarded-Proto if %s%s"
                   % (own, " !{ src -f %s }" % trusted if trusted else ""))
    out += _client_address_rules(plain)
    for inst in elsewhere:
        out.append("    http-request redirect location "
                   "https://%%[req.hdr(host),field(1,:)]:%d%%[pathq] code 301 "
                   "if { var(txn.inst) -m str %s }" % (inst["https_port"], inst["id"]))
    if not plain:
        return out + ["    http-request redirect scheme https code 301"]
    out += ["    http-request redirect scheme https code 301 if { var(txn.inst) -m str %s }"
            % i["id"] for i in secure if i["https_port"] == 443]
    return out + _ROUTING


def _trusted_list(inst):
    mode = inst["trusted_proxies"]
    if mode == "cidrs":
        return _list_path("trusted-%s.lst" % inst["id"])
    if mode:
        return _list_path("cdn-%s.lst" % mode)
    return None


def _render_cfg(instances, thumbprint, listeners, rootless=False):
    """listeners maps each port to its bind addresses (listeners_for)."""
    limits = [backend_limits(i) for i in instances]
    total = sum(d + f for d, f in limits) + sum(
        i["limits"]["media_maxconn"] for i in instances if i["media_hostnames"])
    global_maxconn = max(GLOBAL_MAXCONN_FLOOR, GLOBAL_MAXCONN_FACTOR * total)

    out = [
        "# Generated by Canasta. Do not edit: changes are lost on the next render.",
        "",
        "global",
        "    log stdout format raw local0",
        "    maxconn %d" % global_maxconn,
        # Only the edge's own containers mount the socket's volume; acme.sh
        # may run as the operator rather than root to install certificates.
        "    stats socket %s mode 666 level admin expose-fd listeners" % STATS_SOCKET,
        "    ssl-default-bind-options ssl-min-ver TLSv1.2 no-tls-tickets",
        "",
        "defaults",
        "    mode http",
        "    log global",
        "    option httplog",
        "    timeout connect 5s",
        "    timeout client 60s",
        "    timeout http-request 10s",
        # Varnish waits up to 120s for the first byte from MediaWiki.
        "    timeout server 180s",
        "    timeout queue 30s",
        "",
        # Instances come and go, so their Caddy addresses are looked up at
        # run time rather than once at startup, from the network's resolver,
        # which differs between Docker and Podman.
        "resolvers network",
        "    parse-resolv-conf",
        "    hold valid 10s",
        "",
    ]
    for port, tls in frontends(instances):
        out += _render_frontend(port, tls, instances, thumbprint, listeners[port]) + [""]
    out += [
        "backend be_no_site",
        "    http-request return status 404 content-type text/plain string \"No site is served at this address.\\n\"",
    ]

    for inst, (dynamic_cap, default_cap) in zip(instances, limits):
        server = ("    server caddy caddy-%s:80 resolvers network "
                  "resolve-prefer ipv4 init-addr last,libc,none check maxconn %%d" % inst["id"])
        classes = [("dynamic", dynamic_cap), ("default", default_cap)]
        if inst["media_hostnames"]:
            classes.append(("media", inst["limits"]["media_maxconn"]))
        for kind, cap in classes:
            out += ["", "backend be_%s_%s" % (inst["id"], kind)]
            out += _backend_rules(inst, kind, rootless)
            out += [
                # A stopped instance's Caddy stops resolving; answer at
                # once rather than hold the request for timeout queue.
                "    http-request return status 503 content-type text/plain "
                "string \"This site is not running.\\n\" if { nbsrv eq 0 }",
                server % cap]
            out += ["    %s" % line for line in inst["snippet"]]
        if inst["crowdsec"]:
            out += ["", "backend be_%s_crowdsec" % inst["id"],
                    "    mode tcp",
                    "    timeout connect 2s",
                    "    timeout server 60s",
                    "    server spoa crowdsec-spoa-%s:9000 resolvers network "
                    "resolve-prefer ipv4 init-addr last,libc,none check" % inst["id"]]
    out.append("")
    return "\n".join(out)


def _render_crt_list(entries):
    # The first line is the certificate served when no SNI filter matches.
    lines = [DEFAULT_CERT]
    for entry in entries:
        lines.append("%s/%s %s" % (CERT_DIR, entry["file"], " ".join(entry["hostnames"])))
    return "\n".join(lines) + "\n"


def _render_hosts_map(instances):
    """host@port -> instance/kind, and @port for a port's unnamed instance."""
    lines = []
    for inst in instances:
        for port in _ports_of(inst):
            lines += ["%s@%d %s/wiki" % (h, port, inst["id"]) for h in inst["hostnames"]]
            lines += ["%s@%d %s/media" % (h, port, inst["id"]) for h in inst["media_hostnames"]]
            if inst["unnamed"]:
                lines.append("@%d %s/wiki" % (port, inst["id"]))
    return "\n".join(lines) + "\n"


def _lines(entries):
    return "".join("%s\n" % e for e in entries)


def cdn_providers(instances):
    """The CDNs whose address ranges the render needs."""
    return sorted({i["trusted_proxies"] for i in instances
                   if i["trusted_proxies"] in CDN_HEADERS})


def render(instances, acme_thumbprint=None, cdn_ranges=None, ipv6=False, sockets=False,
           rootless=False):
    """Render the edge's files for the given instances.

    cdn_ranges maps each provider cdn_providers() names to its address
    ranges. ipv6 adds IPv6 listeners, for an edge network with IPv6; with
    sockets, the listeners are the sockets systemd passes HAProxy. rootless
    is a rootless Docker daemon, whose port forwarding hides client
    addresses; per-client limits then apply only where the operator set them.
    Returns {"files": {name: content}, "certificates": [...], "ports":
    [the edge's ports]}. The files go
    in the edge's configuration directory; certificates lists the files the
    crt-list expects in its certs/ directory.
    """
    instances = sorted(instances, key=lambda i: i["id"])
    _check_unique(instances)
    _check_dns_tokens(instances)
    entries = certificates(instances)
    ports = [port for port, _ in frontends(instances)]
    files = {
        "haproxy.cfg": _render_cfg(instances, acme_thumbprint,
                                   listeners_for(ports, ipv6, sockets), rootless),
        "crt-list": _render_crt_list(entries),
        "hosts.map": _render_hosts_map(instances),
    }
    for provider in cdn_providers(instances):
        ranges = (cdn_ranges or {}).get(provider)
        if not ranges:
            raise EdgeError("No address ranges for %s, which "
                            "TRUSTED_PROXIES names." % provider)
        files["cdn-%s.lst" % provider] = _lines(ranges)
    for inst in instances:
        for name, entries_ in (("trusted-%s.lst", inst["trusted_cidrs"]),
                               ("deny-cidr-%s.lst", inst["deny_cidrs"]),
                               ("deny-ua-%s.lst", inst["deny_user_agents"])):
            if entries_:
                files[name % inst["id"]] = _lines(entries_)
        if inst["crowdsec"]:
            files["spoe-%s.cfg" % inst["id"]] = _render_spoe(inst)
    return {"files": files, "certificates": entries, "ports": ports}
