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

import math
import re

HAPROXY_IMAGE = "docker.io/library/haproxy:3.2.25-alpine"

CONFIG_DIR = "/usr/local/etc/haproxy"
CERT_DIR = CONFIG_DIR + "/certs"
DEFAULT_CERT = CERT_DIR + "/default.pem"
STATS_SOCKET = "/var/run/haproxy/admin.sock"

TLS_MODES = ("acme", "internal", "custom")

DEFAULT_PM_MAX_CHILDREN = 25
DYNAMIC_SHARE = 0.6
DEFAULT_FLOOD_FACTOR = 8
# Without Varnish every page view reaches PHP.
DEFAULT_FLOOD_FACTOR_NO_VARNISH = 2
# Idle keep-alive connections count against the process limit as well.
GLOBAL_MAXCONN_FACTOR = 4
GLOBAL_MAXCONN_FLOOR = 2000

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


def instance_from_config(instance_id, env, wikis, media_hosts=(), snippet=""):
    """Build the edge's view of one instance.

    env is the instance's .env as a dict, wikis the parsed wikis.yaml list,
    media_hosts the parsed media-hosts.yaml list, snippet the text of its
    edge snippet (empty when it has none).
    """
    if not _INSTANCE_ID.match(instance_id or ""):
        raise EdgeError("'%s' is not a valid instance ID." % instance_id)

    hostnames = []
    for wiki in wikis or []:
        server = str(wiki.get("url", "")).split("/", 1)[0]
        host = _hostname(server, instance_id,
                         "wiki '%s' URL host" % wiki.get("id", "?"))
        if host not in hostnames:
            hostnames.append(host)
    if not hostnames:
        raise EdgeError("Instance '%s' has no wikis." % instance_id)

    media = []
    for entry in media_hosts or []:
        host = _hostname(entry.get("hostname"), instance_id, "media host")
        if host in hostnames or host in media:
            raise EdgeError(
                "Instance '%s' declares '%s' more than once." % (instance_id, host))
        media.append(host)

    tls_mode = (env.get("CADDY_TLS_MODE") or "acme").strip().lower()
    if tls_mode not in TLS_MODES:
        raise EdgeError(
            "Instance '%s': CADDY_TLS_MODE is '%s'. Valid values are %s."
            % (instance_id, tls_mode, ", ".join("'%s'" % m for m in TLS_MODES)))

    raw_children = (env.get("PM_MAX_CHILDREN") or "").strip()
    try:
        pm_max_children = int(raw_children) if raw_children else DEFAULT_PM_MAX_CHILDREN
    except ValueError:
        pm_max_children = 0
    if pm_max_children < 1:
        raise EdgeError(
            "Instance '%s': PM_MAX_CHILDREN is '%s', not a positive number."
            % (instance_id, raw_children))

    return {
        "id": instance_id,
        "hostnames": hostnames,
        "media_hostnames": media,
        "tls_mode": tls_mode,
        "staging_certs": _truthy(env.get("CANASTA_STAGING_CERTS"), False),
        "tls_cert": (env.get("CADDY_TLS_CERT") or "origin.crt").strip(),
        "tls_key": (env.get("CADDY_TLS_KEY") or "origin.key").strip(),
        "pm_max_children": pm_max_children,
        "varnish": _truthy(env.get("CANASTA_ENABLE_VARNISH"), True),
        "snippet": _snippet_lines(snippet, instance_id),
    }


def backend_limits(instance):
    """Per-server maxconn for the instance's (dynamic, default) backends."""
    children = instance["pm_max_children"]
    dynamic = max(1, int(math.ceil(children * DYNAMIC_SHARE)))
    factor = (DEFAULT_FLOOD_FACTOR if instance["varnish"]
              else DEFAULT_FLOOD_FACTOR_NO_VARNISH)
    return dynamic, children * factor


def certificates(instances):
    """The certificate files the edge serves, one entry per crt-list line.

    acme and internal certificates are one file per hostname. A custom
    certificate is the instance's own, covering all its hostnames.
    """
    entries = []
    for inst in instances:
        names = inst["hostnames"] + inst["media_hostnames"]
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
        for host in inst["hostnames"] + inst["media_hostnames"]:
            if host in owners:
                raise EdgeError(
                    "Instances '%s' and '%s' both serve %s. The edge routes "
                    "each hostname to one instance."
                    % (owners[host], inst["id"], host))
            owners[host] = inst["id"]


def _acme_rule(thumbprint):
    if not thumbprint:
        return []
    return [
        "    http-request return status 200 content-type text/plain "
        "lf-string \"%%[path,field(-1,/)].%s\\n\" "
        "if { path_beg /.well-known/acme-challenge/ }" % thumbprint,
    ]


def _render_cfg(instances, thumbprint):
    limits = [backend_limits(i) for i in instances]
    total = sum(d + f for d, f in limits)
    global_maxconn = max(GLOBAL_MAXCONN_FLOOR, GLOBAL_MAXCONN_FACTOR * total)

    out = [
        "# Generated by Canasta. Do not edit: changes are lost on the next render.",
        "",
        "global",
        "    log stdout format raw local0",
        "    maxconn %d" % global_maxconn,
        "    stats socket %s mode 660 level admin expose-fd listeners" % STATS_SOCKET,
        "    ssl-default-bind-options ssl-min-ver TLSv1.2 no-tls-tickets",
        "",
        "defaults",
        "    mode http",
        "    log global",
        "    option httplog",
        "    option forwardfor",
        "    timeout connect 5s",
        "    timeout client 60s",
        "    timeout http-request 10s",
        # Varnish waits up to 120s for the first byte from MediaWiki.
        "    timeout server 180s",
        "    timeout queue 30s",
        "",
        # Instances come and go, so their Caddy addresses are looked up at
        # run time from Docker's resolver rather than once at startup.
        "resolvers docker",
        "    nameserver dns 127.0.0.11:53",
        "    hold valid 10s",
        "",
        "frontend http",
        "    bind :80",
    ]
    out += _acme_rule(thumbprint)
    out += [
        "    http-request redirect scheme https code 301",
        "",
        "frontend https",
        "    bind :443 ssl crt-list %s/crt-list alpn h2,http/1.1" % CONFIG_DIR,
        "    http-request set-header X-Forwarded-Proto https",
    ]
    out += _acme_rule(thumbprint)
    out += [
        "    http-request set-var(txn.site) req.hdr(host),lower,field(1,:),map(%s/hosts.map)"
        % CONFIG_DIR,
        "    http-request set-var(txn.inst) var(txn.site),field(1,/)",
        "    acl known_site var(txn.inst) -m found",
        "    acl media_host var(txn.site),field(2,/) -m str media",
        # The requests Varnish passes to MediaWiki on every request, plus
        # the expensive views it would cache but crawlers request by the
        # thousand. Images and sitemaps are passed too but are static files.
        "    acl dynamic path_sub /api.php",
        "    acl dynamic path_sub /rest.php/",
        "    acl dynamic req.fhdr(cookie) -m reg ([sS]ession|Token)=",
        "    acl dynamic req.hdr(authorization) -m found",
        "    acl dynamic url_sub -i Special:",
        "    acl dynamic url_sub -i Special%3A",
        "    acl dynamic url_param(diff) -m found",
        "    acl dynamic url_param(oldid) -m found",
        "    acl has_action url_param(action) -m found",
        "    acl view_action url_param(action) -m str view",
        "    use_backend be_%[var(txn.inst)]_dynamic if known_site !media_host dynamic",
        "    use_backend be_%[var(txn.inst)]_dynamic if known_site !media_host !METH_GET",
        "    use_backend be_%[var(txn.inst)]_dynamic if known_site !media_host has_action !view_action",
        "    use_backend be_%[var(txn.inst)]_default if known_site",
        "    default_backend be_no_site",
        "",
        "backend be_no_site",
        "    http-request return status 404 content-type text/plain string \"No site is served at this address.\\n\"",
    ]

    for inst, (dynamic_cap, default_cap) in zip(instances, limits):
        server = ("    server caddy caddy-%s:80 resolvers docker "
                  "init-addr last,libc,none check maxconn %%d" % inst["id"])
        for kind, cap in (("dynamic", dynamic_cap), ("default", default_cap)):
            out += ["", "backend be_%s_%s" % (inst["id"], kind), server % cap]
            out += ["    %s" % line for line in inst["snippet"]]
    out.append("")
    return "\n".join(out)


def _render_crt_list(entries):
    # The first line is the certificate served when no SNI filter matches.
    lines = [DEFAULT_CERT]
    for entry in entries:
        lines.append("%s/%s %s" % (CERT_DIR, entry["file"], " ".join(entry["hostnames"])))
    return "\n".join(lines) + "\n"


def _render_hosts_map(instances):
    lines = []
    for inst in instances:
        lines += ["%s %s/wiki" % (h, inst["id"]) for h in inst["hostnames"]]
        lines += ["%s %s/media" % (h, inst["id"]) for h in inst["media_hostnames"]]
    return "\n".join(lines) + "\n"


def render(instances, acme_thumbprint=None):
    """Render the edge's files for the given instances.

    Returns {"files": {name: content}, "certificates": [...]}. The files go
    in the edge's configuration directory; certificates lists the files the
    crt-list expects in its certs/ directory.
    """
    instances = sorted(instances, key=lambda i: i["id"])
    _check_unique(instances)
    entries = certificates(instances)
    return {
        "files": {
            "haproxy.cfg": _render_cfg(instances, acme_thumbprint),
            "crt-list": _render_crt_list(entries),
            "hosts.map": _render_hosts_map(instances),
        },
        "certificates": entries,
    }
