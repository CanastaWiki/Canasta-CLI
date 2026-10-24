#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module: keep the host's HAProxy edge in line with its instances.

The edge lives in <config dir>/edge. Everything in it is generated from the
instances behind it, so it can be deleted and rebuilt at any time:

  docker-compose.yml   the edge's own Compose project (canasta-edge)
  members.yaml         instances that joined, so a stopped one keeps its route
  conf/                mounted into HAProxy: haproxy.cfg, crt-list, hosts.map
  conf/certs/          one PEM per crt-list line
  ca/                  the local CA that signs internal certificates
  acme/                acme.sh's account, certificates and renewal state

Every Compose instance is behind the edge, its front door.
Its Caddy joins the canasta-edge network through docker-compose.edge.yml,
which also labels the container with the instance's ID and path; running
containers carrying those labels are how members managed by another
controller or user are found.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_edge
short_description: Render and stage the HAProxy edge for Compose instances
description:
  - Writes or removes the instance's docker-compose.edge.yml layer.
  - Records the instance's edge membership, discovers the other members,
    renders the edge configuration, creates missing certificates, checks the
    result with haproxy -c and installs it.
  - Starting, reloading or stopping the edge is left to the caller, guided
    by the returned facts.
options:
  state:
    description: >-
      sync renders and stages the edge. start starts it, or brings it in
      line with its files; stop stops it and removes what runs it. issue
      obtains, renews and removes Let's Encrypt certificates through
      acme.sh, once the edge is running and answering challenges. plan
      decides which of a host's instances move behind the edge; import
      moves an instance's Caddy certificates to the edge. check fails when
      the instance's Caddyfile.site or Caddyfile.global has a tls directive,
      which Caddy cannot use behind the edge. writable fails, naming them,
      when the user running canasta cannot write the edge's files or the
      instance's files the edge writes, and when canasta runs as root on a
      host whose edge belongs to another user.
    type: str
    choices: [sync, start, stop, issue, plan, import, check, writable]
    default: sync
  instance_id:
    description: The instance's registry ID (required for sync and check).
    type: str
  instance_path:
    description: Path to the instance directory (required for sync and check).
    type: str
  member:
    description: >-
      present adds the instance to the edge, absent removes it (and its
      layer), as delete does. Defaults to present.
    type: str
    choices: [present, absent]
  inspect_command:
    description: The container CLI (docker or podman).
    type: str
    default: docker
  edge_dir:
    description: Override the edge directory (default <config dir>/edge).
    type: str
  instances:
    description: >-
      With state=plan, the host's instances as dicts of id, path,
      orchestrator, compose_command and docker_host (the daemon socket the
      instance uses, empty for the default).
    type: list
    elements: dict
  compose_command:
    description: >-
      With state=import, the compose command that reads the instance's
      caddy-data volume; with state=start or stop, the one that runs the
      edge's compose project.
    type: str
    default: docker compose
  recreate:
    description: With state=start, recreate the edge's containers.
    type: bool
    default: false
  restart:
    description: >-
      With state=start under rootless Podman, restart HAProxy's systemd
      service, for changed units.
    type: bool
    default: false
  retry:
    description: >-
      With state=issue, request a certificate whose HTTP-01 validation
      failed once more, as for the edge's first Let's Encrypt account.
    type: bool
    default: false
"""

import base64
import calendar
import fcntl
import http.client
import io
import ipaddress
import json
import os
import pwd
import re
import shlex
import shutil
import stat
import tarfile
import tempfile
import time

import yaml

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_config import get_config_dir, is_k8s_orchestrator
from ansible.module_utils.canasta_env_keys import modernize_values
from ansible.module_utils.canasta_edge_render import (
    ACME_IMAGE, CERT_DIR, CONFIG_DIR, DENY_CIDR_FILE, DENY_UA_FILE, EdgeError,
    HAPROXY_IMAGE, SPOA_IMAGE, acme_hostnames, cdn_providers, certificates,
    check_together, crowdsec_enforced, frontends, host_tls_mode,
    instance_from_config, rates_set, render,
)
from ansible.module_utils.canasta_caddyfile import (
    instance_tls_directives, site_hostnames, tls_directives_problem,
)
from ansible.module_utils.urls import open_url

NETWORK = "canasta-edge"
CONTAINER = "canasta-edge"
PROJECT = "canasta-edge"
LAYER_FILE = "docker-compose.edge.yml"
SNIPPET_FILE = os.path.join("config", "haproxy-backend.cfg")
LABEL_ID = "canasta.edge.instance"
LABEL_PATH = "canasta.edge.path"
CONF_FILES = ("haproxy.cfg", "crt-list", "hosts.map")
INTERNAL_CERT_DAYS = 825
ACME_CONTAINER = "canasta-edge-acme"
ACME_SERVERS = {False: "letsencrypt", True: "letsencrypt_test"}
# As acme.sh --list names the CA that issued a certificate.
ACME_CA_NAMES = {False: "LetsEncrypt.org", True: "LetsEncrypt.org_test"}
# Both Let's Encrypt CAs get the same account key, so one thumbprint answers
# challenges for staging and production hostnames on the same edge.
ACME_CA_DIRS = ("acme-v02.api.letsencrypt.org", "acme-staging-v02.api.letsencrypt.org")
# acme.sh's HAProxy hook replaces a certificate over the stats socket under
# the path HAProxy loaded it from, so the acme.sh container sees the
# certificates at HAProxy's path.
ACME_DEPLOY_ENV = {
    "DEPLOY_HAPROXY_PEM_PATH": CERT_DIR,
    "DEPLOY_HAPROXY_HOT_UPDATE": "yes",
    "DEPLOY_HAPROXY_STATS_SOCKET": "UNIX:/var/run/haproxy/admin.sock",
}

# Where each CDN publishes its address ranges, and the ranges as of this
# release, used when the host cannot reach the source and has no earlier copy.
CDN_SOURCES = {
    "cloudflare": ("https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6"),
    "imperva": ("https://my.imperva.com/api/integration/v1/ips",),
}
BUNDLED_CDN_RANGES = {
    "cloudflare": [
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
        "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
    ],
    "imperva": [
        "199.83.128.0/21", "198.143.32.0/19", "149.126.72.0/21", "103.28.248.0/22",
        "185.11.124.0/22", "192.230.64.0/18", "45.64.64.0/22", "107.154.0.0/16",
        "45.60.0.0/16", "45.223.0.0/16", "131.125.128.0/17", "2a02:e980::/29",
    ],
}

EDGE_COMPOSE = """\
# Generated by Canasta. Do not edit.
services:
  haproxy:
    image: %(image)s
    container_name: %(container)s
    # Reads the private keys in conf/certs, which are not world-readable.
    user: root
    # The edge is only ever removed, never left stopped; "always" also
    # brings back a container Docker counts as manually stopped.
    restart: always
    # HAProxy logs every request.
    logging: &logging
      driver: json-file
      options:
        max-size: "50m"
        max-file: "3"
    ports:
%(ports)s    volumes:
      - ./conf:%(config_dir)s:ro
      - run:/var/run/haproxy
    networks:
      edge: {}
  # Obtains and renews Let's Encrypt certificates. HAProxy answers the
  # challenges itself (acme.sh's stateless mode).
  acme:
    image: %(acme_image)s
    container_name: %(acme_container)s
    user: "%(acme_user)s"
    command: daemon
    restart: unless-stopped
    logging: *logging
    volumes:
      - ./acme:/acme.sh
      - ./conf/certs:%(cert_dir)s
      - run:/var/run/haproxy
networks:
  edge:
    name: %(network)s
%(ipv6)svolumes:
  run:
"""

# Under rootless Podman, HAProxy runs from systemd units (SERVICE_UNIT_TEXT)
# instead; compose keeps acme.sh, the network and the runtime-socket volume.
EDGE_COMPOSE_SOCKETS = """\
# Generated by Canasta. Do not edit.
# HAProxy runs from the canasta-edge systemd user units.
services:
  # Obtains and renews Let's Encrypt certificates. HAProxy answers the
  # challenges itself (acme.sh's stateless mode).
  acme:
    image: %(acme_image)s
    container_name: %(acme_container)s
    user: "%(acme_user)s"
    command: daemon
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "3"
    restart: unless-stopped
    volumes:
      - ./acme:/acme.sh
      - ./conf/certs:%(cert_dir)s
      - run:/var/run/haproxy
    networks:
      edge: {}
networks:
  edge:
    name: %(network)s
%(ipv6)svolumes:
  run:
    name: %(volume)s
"""

# Rootless Podman's port forwarder replaces each client's address, so under
# it systemd owns the edge's ports and passes the listening sockets to
# HAProxy, which binds them in this order (see listeners_for).
SOCKET_UNIT = "canasta-edge.socket"
SERVICE_UNIT = "canasta-edge.service"
RUN_VOLUME = "canasta-edge-run"
SOCKET_UNIT_TEXT = """\
# Generated by Canasta. Do not edit.
[Unit]
Description=Canasta HAProxy edge listeners

[Socket]
%(listen)sBindIPv6Only=ipv6-only

[Install]
WantedBy=sockets.target
"""
SERVICE_UNIT_TEXT = """\
# Generated by Canasta. Do not edit.
[Unit]
Description=Canasta HAProxy edge
Requires=%(socket)s
After=%(socket)s

[Service]
ExecStartPre=-%(podman)s rm -f %(container)s
# Reads the private keys in conf/certs, which are not world-readable.
# HAProxy logs every request: keep `podman logs` working, within bounds.
ExecStart=%(podman)s run --rm --name %(container)s --network %(network)s --user root \\
    --log-driver k8s-file --log-opt max-size=50m \\
    -v %(conf)s:%(config_dir)s:ro -v %(volume)s:/var/run/haproxy %(image)s
ExecStop=%(podman)s stop -t 10 %(container)s
Restart=on-failure
TimeoutStartSec=300

[Install]
WantedBy=default.target
"""

# Without IPv6 on the edge network, Docker hands IPv6 connections to
# HAProxy through its userland proxy, which replaces the client's address
# with the network gateway's. A fixed ULA subnet, since older Docker cannot
# pick one itself.
IPV6_NETWORK = """\
    enable_ipv6: true
    ipam:
      config:
        - subnet: fd63:616e:6173:7461::/64
"""

LAYER = """\
# Generated by Canasta. Do not edit.
services:
  caddy:
    networks:
      %(network)s:
        aliases:
          - caddy-%(id)s
    labels:
      %(label_id)s: "%(id)s"
      %(label_path)s: "%(path)s"
%(spoa)snetworks:
  %(network)s:
    external: true
"""

# The instance's CrowdSec bouncer for the edge, on both networks: its LAPI
# is on the instance's, and the edge asks it on the edge's. The image
# declares its HAProxy Lua scripts and ban-page templates as volumes, which
# the edge does not use (it answers a banned client with a plain 403); tmpfs
# over them keeps Docker from leaving two anonymous volumes behind each time
# the container is created.
SPOA_SERVICE = """\
  crowdsec-spoa:
    image: %(image)s
    restart: unless-stopped
    environment:
      - CROWDSEC_URL=http://crowdsec:8080/
      - CROWDSEC_KEY=${CROWDSEC_SPOA_API_KEY:-}
      - PROMETHEUS_ENABLED=false
    tmpfs:
      - /usr/lib/crowdsec-haproxy-spoa-bouncer/lua
      - /var/lib/crowdsec-haproxy-spoa-bouncer/html
    networks:
      default: {}
      %(network)s:
        aliases:
          - crowdsec-spoa-%(id)s
"""


def _read(path):
    try:
        with open(path) as handle:
            return handle.read()
    except (IOError, OSError):
        return None


def _write_if_changed(path, content, mode=0o644):
    """Write content atomically when it differs. Returns True if written."""
    if _read(path) == content:
        return False
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    os.chmod(tmp, mode)
    os.rename(tmp, path)
    return True


def read_env(instance_path):
    """The instance's .env as a dict, with surrounding quotes removed."""
    env = {}
    for line in (_read(os.path.join(instance_path, ".env")) or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return modernize_values(env)


def write_layer(instance_path, instance_id, present, spoa=False):
    """Write or remove the instance's edge layer, with its CrowdSec bouncer
    when spoa. Returns True if changed."""
    path = os.path.join(instance_path, LAYER_FILE)
    if not present:
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
    return _write_if_changed(path, LAYER % {
        "network": NETWORK, "id": instance_id, "path": instance_path,
        "label_id": LABEL_ID, "label_path": LABEL_PATH,
        "spoa": SPOA_SERVICE % {"image": SPOA_IMAGE, "network": NETWORK,
                                "id": instance_id} if spoa else "",
    })


def remove_stale_spoa(run, inspect, instance_path):
    """Remove the instance's CrowdSec bouncer for the edge once its layer no
    longer has it: compose up leaves a service it no longer knows running."""
    project = os.path.basename(instance_path.rstrip("/")).lower()
    rc, out, _ = run([inspect, "ps", "-aq",
                      "--filter", "label=com.docker.compose.project=%s" % project,
                      "--filter", "label=com.docker.compose.service=crowdsec-spoa"])
    ids = out.split() if rc == 0 else []
    if ids:
        run([inspect, "rm", "-f", "-v"] + ids)
    return bool(ids)


# What Docker copied into the anonymous volumes it made for the bouncer
# image's declared volumes, before the layer mounted tmpfs over them.
SPOA_LEFTOVER_CONTENTS = (
    {"crowdsec.lua", "template.lua", "utils.lua"},
    {"ban.html", "ban-with-contact.html", "captcha.html", "lua"},
)
_ANONYMOUS_VOLUME = re.compile(r"^[0-9a-f]{64}$")
PRUNE_IMAGE = "docker.io/library/alpine:3.21"


def prune_spoa_volumes(run, inspect):
    """Remove the anonymous volumes earlier bouncer containers left behind.

    Only an unattached volume with a generated name and exactly the files the
    bouncer image put in one of its two volumes is removed, so no volume an
    operator or another container made can match. Returns the removed names.
    """
    rc, out, _ = run([inspect, "volume", "ls", "-q", "--filter", "dangling=true"])
    names = [n for n in out.split() if _ANONYMOUS_VOLUME.match(n)] if rc == 0 else []
    if not names:
        return []
    argv = [inspect, "run", "--rm", "--network", "none"]
    for i, name in enumerate(names):
        argv += ["-v", "%s:/v/%d:ro" % (name, i)]
    argv += [PRUNE_IMAGE, "sh", "-c",
             'for d in /v/*; do echo "${d#/v/} $(ls -A "$d" | tr \'\\n\' \' \')"; done']
    rc, out, _ = run(argv)
    if rc != 0:
        return []
    leftovers = []
    for line in out.splitlines():
        index, _, files = line.strip().partition(" ")
        if index.isdigit() and int(index) < len(names) \
                and set(files.split()) in SPOA_LEFTOVER_CONTENTS:
            leftovers.append(names[int(index)])
    if leftovers:
        run([inspect, "volume", "rm"] + leftovers)
    return leftovers


def read_members(edge_dir):
    data = yaml.safe_load(_read(os.path.join(edge_dir, "members.yaml")) or "") or {}
    return {m["id"]: m["path"] for m in data.get("members") or []
            if m.get("id") and m.get("path")}


def write_members(edge_dir, members):
    content = yaml.safe_dump(
        {"members": [{"id": i, "path": members[i]} for i in sorted(members)]},
        default_flow_style=False)
    return _write_if_changed(os.path.join(edge_dir, "members.yaml"), content)


def discover(run, inspect):
    """{id: path} for running containers labeled as edge members."""
    rc, out, _ = run([inspect, "ps", "-q", "--filter", "network=%s" % NETWORK])
    ids = out.split() if rc == 0 else []
    if not ids:
        return {}
    fmt = '{{index .Config.Labels "%s"}}|{{index .Config.Labels "%s"}}' % (
        LABEL_ID, LABEL_PATH)
    rc, out, _ = run([inspect, "inspect", "--format", fmt] + ids)
    found = {}
    for line in out.splitlines() if rc == 0 else []:
        inst_id, _, path = line.strip().partition("|")
        if inst_id and path and inst_id != "<no value>":
            found[inst_id] = path
    return found


def edge_running(run, inspect, container=CONTAINER):
    rc, out, _ = run([inspect, "ps", "-q", "--filter", "name=^%s$" % container])
    return rc == 0 and bool(out.strip())


def global_sites(path):
    """(hostnames, skipped) for the site blocks in the instance's
    Caddyfile.global, which its Caddy serves besides the wikis."""
    return site_hostnames(_read(os.path.join(path, "config", "Caddyfile.global")) or "")


def tls_directives(path):
    """'file:line' for each tls directive in the instance's Caddyfile.site
    and Caddyfile.global."""
    return instance_tls_directives(lambda rel: _read(os.path.join(path, rel)))


def load_instance(instance_id, path):
    """The renderer's view of an instance, or None when it is not (or no
    longer) behind the edge."""
    env = read_env(path)
    if not os.path.isdir(path):
        return None
    wikis = (yaml.safe_load(_read(os.path.join(path, "config", "wikis.yaml")) or "")
             or {}).get("wikis") or []
    media = (yaml.safe_load(_read(os.path.join(path, "config", "media-hosts.yaml")) or "")
             or {}).get("media_hosts") or []
    # A gitops template renders an entry this host does not serve with an
    # empty hostname.
    media = [m for m in media if (m.get("hostname") or "").strip()]
    snippet = _read(os.path.join(path, SNIPPET_FILE)) or ""
    inst = instance_from_config(
        instance_id, env, wikis, media, snippet,
        deny_user_agents=_read(os.path.join(path, DENY_UA_FILE)) or "",
        deny_cidrs=_read(os.path.join(path, DENY_CIDR_FILE)) or "",
        extra_hostnames=global_sites(path)[0])
    inst["path"] = path
    return inst


def ensure_image(run, inspect, image):
    """Pull image unless it is present, so a registry failure is reported
    as one rather than as the failure of whatever runs it."""
    if run([inspect, "image", "inspect", image])[0] == 0:
        return
    rc, out, err = run([inspect, "pull", "-q", image])
    if rc != 0:
        raise EdgeError("Could not pull %s: %s" % (image, _tail(out, err)))


def helper_user(run, inspect, edge_dir):
    """The user the edge's helper containers (openssl, acme.sh) run as.

    The edge directory belongs to whoever runs canasta. Where container
    root is host root (rootful Docker or Podman) and that is not root, a
    helper running as root would leave root-owned files and directories
    the operator cannot remove, so helpers run as the operator. Elsewhere
    (rootless runtimes, or canasta run as root) root already maps to the
    operator. Decided by having a container create a file, and kept with
    the ID of the daemon and the uid it was decided for, so a host that
    moves between rootful and rootless Docker, or a run by another user,
    decides again.
    """
    cached = os.path.join(edge_dir, ".helper-user")
    cached_daemon = os.path.join(edge_dir, ".helper-user-daemon")
    rc, out, _ = run([inspect, "info", "--format", "{{.ID}}"])
    key = "%s\n%d" % (out.strip() if rc == 0 else "", os.getuid())
    user = (_read(cached) or "").strip()
    if user and (_read(cached_daemon) or "").strip() == key:
        return user
    user = "root"
    if os.getuid() != 0:
        probe = os.path.join(edge_dir, ".owner-probe")
        ensure_image(run, inspect, ACME_IMAGE)
        run([inspect, "run", "--rm", "-v", "%s:/probe" % edge_dir,
             "--entrypoint", "touch", ACME_IMAGE, "/probe/.owner-probe"])
        if os.path.exists(probe):
            if os.stat(probe).st_uid == 0:
                user = "%d:%d" % (os.getuid(), os.getgid())
            run([inspect, "run", "--rm", "-v", "%s:/probe" % edge_dir,
                 "--entrypoint", "rm", ACME_IMAGE, "-f", "/probe/.owner-probe"])
    _write_if_changed(cached, user + "\n")
    _write_if_changed(cached_daemon, key + "\n")
    return user


def _fetch(url):
    """The response body, or None. Imperva answers a POST with JSON."""
    try:
        if "imperva" in url:
            body = open_url(url, method="POST", data="resp_format=json",
                            timeout=5).read().decode()
            data = json.loads(body)
            return "\n".join(data.get("ipRanges", []) + data.get("ipv6Ranges", []))
        return open_url(url, timeout=5).read().decode()
    except Exception:  # noqa: BLE001 - any failure falls back to a known list
        return None


def _valid_ranges(text):
    ranges = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            ranges.append(str(ipaddress.ip_network(line, strict=False)))
        except ValueError:
            return []
    return ranges


def cdn_ranges(edge_dir, providers):
    """Each CDN's current address ranges: fetched from its source, else the
    copy the edge already serves, else the list bundled with this release."""
    result = {}
    for provider in providers:
        fetched = [_fetch(url) for url in CDN_SOURCES[provider]]
        ranges = [] if None in fetched else _valid_ranges("\n".join(fetched))
        if not ranges:
            ranges = _valid_ranges(_read(os.path.join(
                edge_dir, "conf", "cdn-%s.lst" % provider)))
        result[provider] = ranges or BUNDLED_CDN_RANGES[provider]
    return result


def _openssl_script(entries, ca_missing):
    """Shell for the openssl container: the CA and the default certificate
    when missing, then one CA-signed certificate per entry."""
    lines = ["set -e", "cd /work"]
    if ca_missing:
        lines.append(
            "openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 "
            "-nodes -days 3650 -subj '/CN=Canasta edge CA' "
            "-keyout /ca/ca.key -out /ca/ca.crt 2>/dev/null")
    for name, hosts in entries:
        san = ",".join("DNS:%s" % h for h in hosts)
        lines += [
            "openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes "
            "-subj '/CN=%s' -keyout key.pem -out req.csr 2>/dev/null" % hosts[0],
            "printf 'subjectAltName=%s\\n' > ext.cnf" % san,
            "openssl x509 -req -in req.csr -CA /ca/ca.crt -CAkey /ca/ca.key "
            "-CAcreateserial -days %d -extfile ext.cnf -out cert.pem 2>/dev/null"
            % INTERNAL_CERT_DAYS,
            "cat cert.pem key.pem > /certs/%s" % name,
            "chmod 600 /certs/%s" % name,
            "rm -f key.pem req.csr ext.cnf cert.pem",
        ]
    return "\n".join(lines)


def ensure_certificates(run, inspect, edge_dir, certificates, instances):
    """Create what the crt-list needs and remove what it no longer names.
    Returns the names of the files written or removed."""
    certs = os.path.join(edge_dir, "conf", "certs")
    ca = os.path.join(edge_dir, "ca")
    changed = []
    paths = {i["id"]: i["path"] for i in instances}

    for entry in certificates:
        if entry["mode"] != "custom":
            continue
        src = os.path.join(paths[entry["instance"]], "config", "certs")
        parts = [_read(os.path.join(src, entry["cert"])),
                 _read(os.path.join(src, entry["key"]))]
        if None in parts:
            raise EdgeError(
                "Instance '%s': TLS_MODE is 'custom' but %s or %s is "
                "missing from %s." % (entry["instance"], entry["cert"],
                                      entry["key"], src))
        pem = "".join(p if p.endswith("\n") else p + "\n" for p in parts)
        if _write_if_changed(os.path.join(certs, entry["file"]), pem, 0o600):
            changed.append(entry["file"])

    # acme certificates are issued by acme.sh; until one exists, the host is
    # served a CA-signed one like internal.
    wanted = [(e["file"], e["hostnames"]) for e in certificates
              if e["mode"] != "custom"
              and not os.path.exists(os.path.join(certs, e["file"]))]
    if not os.path.exists(os.path.join(certs, "default.pem")):
        wanted.insert(0, ("default.pem", ["default"]))
    if wanted:
        ca_missing = not os.path.exists(os.path.join(ca, "ca.crt"))
        # Under the edge directory, which is the same path on the host when
        # the CLI itself runs in a container; its own /tmp is not.
        ensure_image(run, inspect, ACME_IMAGE)
        work = tempfile.mkdtemp(prefix=".work-", dir=edge_dir)
        try:
            rc, _, err = run([
                inspect, "run", "--rm", "--user", helper_user(run, inspect, edge_dir),
                "-v", "%s:/certs" % certs, "-v", "%s:/ca" % ca,
                "-v", "%s:/work" % work, "--entrypoint", "sh", ACME_IMAGE,
                "-c", _openssl_script(wanted, ca_missing)])
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if rc != 0:
            raise EdgeError("Could not create the edge certificates: %s" % err.strip())
        changed += [name for name, _ in wanted]

    keep = {e["file"] for e in certificates} | {"default.pem"}
    for name in sorted(os.listdir(certs)):
        if name.endswith(".pem") and name not in keep:
            os.remove(os.path.join(certs, name))
            changed.append(name)
    return changed


def validate(run, inspect, edge_dir, files):
    """Check the rendered files with haproxy -c before they are installed."""
    ensure_image(run, inspect, HAPROXY_IMAGE)
    stage = tempfile.mkdtemp(prefix=".stage-", dir=edge_dir)
    try:
        for name, content in files.items():
            if name == "haproxy.cfg":
                # The check runs without the sockets systemd passes the
                # edge, so it binds loopback ports in their place.
                content = re.sub(r"bind fd@(\d+)",
                                 lambda m: "bind 127.0.0.1:%d" % (18000 + int(m.group(1))),
                                 content)
            with open(os.path.join(stage, name), "w") as handle:
                handle.write(content)
        # The certificates are mounted over this; Docker cannot create the
        # mount point inside a read-only mount.
        os.mkdir(os.path.join(stage, "certs"))
        os.chmod(stage, 0o755)
        rc, out, err = run([
            inspect, "run", "--rm", "--user", "root",
            "-v", "%s:%s:ro" % (stage, CONFIG_DIR),
            "-v", "%s:%s:ro" % (os.path.join(edge_dir, "conf", "certs"), CERT_DIR),
            HAPROXY_IMAGE, "haproxy", "-c", "-f", CONFIG_DIR + "/haproxy.cfg"])
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if rc != 0:
        raise EdgeError("The rendered edge configuration is invalid:\n%s"
                        % (err or out).strip())


def _locked(edge_dir, func, *args):
    """Run func holding the edge's lock. Instances on one host share the
    edge, and commands for two of them can run at once."""
    os.makedirs(os.path.dirname(edge_dir) or ".", exist_ok=True)
    with open(edge_dir + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return func(*args)


def _user_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return "uid %d" % uid


def _unwritable(path):
    """Whether the user running canasta neither owns path nor can write it."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (not stat.S_ISLNK(st.st_mode) and st.st_uid != os.getuid()
            and not os.access(path, os.W_OK))


def unwritable_paths(edge_dir, instance_path=None):
    """What the edge flow writes that the user running canasta cannot: the
    edge directory's tree, its lock, and the instance's files."""
    lock = edge_dir + ".lock"
    paths = [lock if os.path.lexists(lock) else os.path.dirname(edge_dir)]
    for root, _, files in os.walk(edge_dir):
        paths += [root] + [os.path.join(root, f) for f in sorted(files)]
    if instance_path:
        paths += [instance_path] + [os.path.join(instance_path, p) for p in (
            "config", LAYER_FILE, SNIPPET_FILE, os.path.join(".git", "index"))]
    return [p for p in paths if _unwritable(p)]


def foreign_owner(edge_dir):
    """The user the edge belongs to, when canasta runs as root and the edge
    (or the directory it goes in) belongs to someone else; else None."""
    if os.getuid() != 0:
        return None
    for path in (edge_dir, os.path.dirname(edge_dir)):
        if os.path.isdir(path):
            uid = os.stat(path).st_uid
            return _user_name(uid) if uid != 0 else None
    return None


UNWRITABLE_SHOWN = 10


def check_writable(edge_dir, instance_path=None):
    """Raise EdgeError when this run would leave files the edge's owner
    cannot change, or cannot change the edge's files itself."""
    owner = foreign_owner(edge_dir)
    if owner:
        raise EdgeError(
            "canasta is running as root, but the HAProxy edge in %s belongs to %s. "
            "Run canasta as %s: as root it would write files %s could not change."
            % (edge_dir, owner, owner, owner))
    paths = unwritable_paths(edge_dir, instance_path)
    if not paths:
        return
    me = _user_name(os.getuid())
    shown = paths[:UNWRITABLE_SHOWN]
    more = len(paths) - len(shown)
    raise EdgeError(
        "%s cannot write %s%s, which the HAProxy edge needs to update. Make %s "
        "their owner again (for example, with sudo chown) and run the command again."
        % (me, ", ".join(shown), " and %d more" % more if more else "", me))


def sync(run, inspect, edge_dir, instance_id, instance_path, member):
    """Bring the edge in line. Returns the facts the caller acts on."""
    return _locked(edge_dir, _sync, run, inspect, edge_dir, instance_id,
                   instance_path, member)


def _load_members(edge_dir, members):
    instances = []
    for inst_id in sorted(members):
        inst = load_instance(inst_id, members[inst_id])
        if inst is not None:
            instances.append(inst)
    return instances


def ensure_account(run, inspect, edge_dir):
    """The ACME account's thumbprint, registering the account first if this
    edge has none. The thumbprint is what HAProxy answers challenges with."""
    path = os.path.join(edge_dir, "acme", "thumbprint")
    thumbprint = (_read(path) or "").strip()
    if thumbprint:
        return thumbprint
    lines = ["set -e",
             "openssl ecparam -name prime256v1 -genkey -noout -out /tmp/account.key 2>/dev/null"]
    for ca in ACME_CA_DIRS:
        lines += ["mkdir -p /acme.sh/ca/%s/directory" % ca,
                  "[ -f /acme.sh/ca/%s/directory/account.key ] || "
                  "cp /tmp/account.key /acme.sh/ca/%s/directory/account.key" % (ca, ca)]
    lines += ["acme.sh --register-account --server %s" % server
              for server in ACME_SERVERS.values()]
    ensure_image(run, inspect, ACME_IMAGE)
    rc, out, err = run([inspect, "run", "--rm",
                        "--user", helper_user(run, inspect, edge_dir),
                        "-v", "%s:/acme.sh" % os.path.join(edge_dir, "acme"),
                        "--entrypoint", "sh", ACME_IMAGE, "-c", "\n".join(lines)])
    found = sorted({line.split("ACCOUNT_THUMBPRINT=", 1)[1].strip().strip("'\"")
                    for line in (out + err).splitlines() if "ACCOUNT_THUMBPRINT=" in line})
    if rc != 0 or len(found) != 1:
        raise EdgeError("Could not register the edge's Let's Encrypt account:\n%s"
                        % (err or out).strip()[-2000:])
    _write_if_changed(path, found[0] + "\n", 0o600)
    return found[0]


def network_ipv6(run, inspect):
    """Whether the existing edge network has IPv6, or None when there is
    none."""
    rc, out, _ = run([inspect, "network", "inspect", NETWORK, "--format",
                      "{{.EnableIPv6}}"])
    if rc != 0:
        return None
    return out.strip().lower() == "true"


def uses_sockets(inspect):
    """Whether the edge takes its listening sockets from systemd: under
    rootless Podman, the only Podman setup the CLI supports."""
    return "podman" in os.path.basename(inspect or "")


def _user_env():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    return {"XDG_RUNTIME_DIR": runtime,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=%s/bus" % runtime}


def systemctl(run, *args):
    return run(["systemctl", "--user"] + list(args), env=_user_env())


def user_systemd_available(run):
    """Whether the operator's systemd user manager answers; with lingering
    it runs without a login session."""
    return systemctl(run, "show-environment")[0] == 0


def units_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "systemd", "user")


def _socket_listens(ports, ipv6):
    """ListenStream lines in the order HAProxy binds them (listeners_for):
    each port over IPv4, then each over IPv6."""
    ports = sorted(ports)
    lines = ["ListenStream=0.0.0.0:%d" % p for p in ports]
    if ipv6:
        lines += ["ListenStream=[::]:%d" % p for p in ports]
    return "".join(line + "\n" for line in lines)


def write_units(inspect, edge_dir, ipv6, ports=(80, 443)):
    """Write the edge's systemd user units. Returns True if either changed."""
    directory = units_dir()
    os.makedirs(directory, exist_ok=True)
    podman = shutil.which(inspect) or inspect
    changed = _write_if_changed(os.path.join(directory, SOCKET_UNIT), SOCKET_UNIT_TEXT % {
        "listen": _socket_listens(ports, ipv6)})
    return _write_if_changed(os.path.join(directory, SERVICE_UNIT), SERVICE_UNIT_TEXT % {
        "socket": SOCKET_UNIT, "podman": podman, "container": CONTAINER, "network": NETWORK,
        "conf": os.path.join(edge_dir, "conf"), "config_dir": CONFIG_DIR,
        "volume": RUN_VOLUME, "image": HAPROXY_IMAGE}) or changed


def start_edge(run, inspect, compose_command, edge_dir, recreate=False, restart=False):
    """Start the edge, or bring it in line with its files."""
    argv = shlex.split(compose_command) + ["-p", "canasta-edge", "up", "-d"]
    if recreate:
        argv.append("--force-recreate")
    rc, out, err = run(argv, cwd=edge_dir)
    if rc != 0:
        raise EdgeError("Could not start the HAProxy edge: %s" % _tail(out, err))
    if not uses_sockets(inspect):
        return
    if not user_systemd_available(run):
        raise EdgeError(
            "The HAProxy edge needs the operator's systemd user session under rootless "
            "Podman, to take ports 80 and 443 with their clients' addresses. Enable "
            "lingering for this user ('canasta install podman' does).")
    # An edge HAProxy that compose ran holds 80 and 443 through Podman's
    # forwarder, which the socket unit could not then bind.
    if systemctl(run, "is-active", "--quiet", SOCKET_UNIT)[0] != 0:
        run([inspect, "rm", "-f", CONTAINER])
    ensure_image(run, inspect, HAPROXY_IMAGE)
    for args in (["daemon-reload"], ["enable", "--now", SOCKET_UNIT], ["enable", SERVICE_UNIT],
                 ["restart" if (restart or recreate) else "start", SERVICE_UNIT]):
        rc, out, err = systemctl(run, *args)
        if rc != 0:
            raise EdgeError("Could not start the HAProxy edge (systemctl --user %s): %s"
                            % (" ".join(args), _tail(out, err)))
    # Certificates are requested right after this, and HAProxy answers the
    # challenges.
    for _ in range(30):
        if edge_running(run, inspect):
            return
        time.sleep(1)
    raise EdgeError("The HAProxy edge did not start; see 'systemctl --user status %s'."
                    % SERVICE_UNIT)


def stop_edge(run, inspect, compose_command, edge_dir):
    """Stop the edge and remove what runs it.

    Also removes conf/, which names the hostnames of instances no longer
    behind the edge and holds their private keys. The CA and acme.sh's
    account stay for the next instance on the host.
    """
    if uses_sockets(inspect):
        systemctl(run, "disable", "--now", SERVICE_UNIT, SOCKET_UNIT)
        for unit in (SERVICE_UNIT, SOCKET_UNIT):
            path = os.path.join(units_dir(), unit)
            if os.path.exists(path):
                os.remove(path)
        systemctl(run, "daemon-reload")
    # -v: the edge's only volume holds its runtime socket.
    rc, out, err = run(shlex.split(compose_command) + ["-p", "canasta-edge", "down", "-v"],
                       cwd=edge_dir)
    if rc != 0:
        raise EdgeError("Could not stop the HAProxy edge: %s" % _tail(out, err))
    shutil.rmtree(os.path.join(edge_dir, "conf"), ignore_errors=True)


def host_has_ipv6():
    """Whether the kernel has IPv6 enabled, so the edge network can use it."""
    try:
        with open("/proc/sys/net/ipv6/conf/all/disable_ipv6") as f:
            return f.read().strip() == "0"
    except OSError:
        return False


def _sync(run, inspect, edge_dir, instance_id, instance_path, member):
    result = {"changed": False, "edge_dir": edge_dir, "reload": False,
              "compose_changed": False, "acme": False}
    env = read_env(instance_path)
    if member is None:
        member = "present"
    spoa = member == "present" and crowdsec_enforced(env)
    had_spoa = "crowdsec-spoa:" in (_read(os.path.join(instance_path, LAYER_FILE)) or "")
    result["changed"] |= write_layer(instance_path, instance_id, member == "present", spoa)
    if had_spoa and not spoa:
        result["changed"] |= remove_stale_spoa(run, inspect, instance_path)

    if member == "absent" and not os.path.isdir(edge_dir):
        result.update(members=[], running=False)
        return result
    try:
        return _sync_edge(run, inspect, edge_dir, instance_id, instance_path,
                          member, result)
    except EdgeError as exc:
        # The edge keeps serving its last good configuration, which still
        # routes every instance it did; the caller decides when to fail.
        result.update(error=str(exc), reload=False, acme=False,
                      compose_changed=False, units_changed=False,
                      members=sorted(read_members(edge_dir)),
                      running=edge_running(run, inspect),
                      acme_running=edge_running(run, inspect, ACME_CONTAINER))
        return result


def _sync_edge(run, inspect, edge_dir, instance_id, instance_path, member, result):
    for sub in ("conf/certs", "ca", "acme"):
        os.makedirs(os.path.join(edge_dir, sub), mode=0o700, exist_ok=True)
    os.chmod(os.path.join(edge_dir, "conf"), 0o755)

    members = read_members(edge_dir)
    members.update(discover(run, inspect))
    members.pop(instance_id, None)
    if member == "present":
        members[instance_id] = instance_path

    instances = _load_members(edge_dir, members)
    members = {i["id"]: i["path"] for i in instances}
    # Site addresses the edge cannot route by name stay with Caddy, which
    # can no longer obtain certificates for them behind the edge.
    result["unrouted_sites"] = [
        {"instance": i["id"], "address": addr, "reason": reason}
        for i in instances for addr, reason in global_sites(i["path"])[1]]
    result["changed"] |= write_members(edge_dir, members)
    result["members"] = sorted(members)
    result["running"] = edge_running(run, inspect)
    # An existing network keeps its IPv6 setting: Compose cannot change it
    # while instances' Caddy containers are attached. It goes, and comes
    # back with the host's setting, when no instance is behind the edge.
    ipv6 = host_has_ipv6()
    existing = network_ipv6(run, inspect)
    if existing is not None and existing != ipv6:
        result["network_ipv6_pending"] = ipv6
        ipv6 = existing
    result["acme_running"] = edge_running(run, inspect, ACME_CONTAINER)

    # The ports every instance behind the edge is served on.
    ports = [port for port, _ in frontends(instances)] if instances else [80, 443]
    result["ports"] = ports
    compose_file = os.path.join(edge_dir, "docker-compose.yml")
    # A new edge directory under running edge containers (it was deleted):
    # they still mount the old one, and an unchanged compose file would not
    # make `up` replace them.
    result["recreate"] = not os.path.exists(compose_file)
    sockets = uses_sockets(inspect)
    result["compose_changed"] = _write_if_changed(
        compose_file,
        (EDGE_COMPOSE_SOCKETS if sockets else EDGE_COMPOSE) % {
            "image": HAPROXY_IMAGE, "container": CONTAINER,
            "config_dir": CONFIG_DIR, "network": NETWORK,
            "acme_image": ACME_IMAGE, "acme_container": ACME_CONTAINER,
            "acme_user": helper_user(run, inspect, edge_dir),
            "cert_dir": CERT_DIR, "volume": RUN_VOLUME,
            "ports": "".join('      - "%d:%d"\n' % (p, p) for p in ports),
            "ipv6": IPV6_NETWORK if ipv6 else ""})
    result["changed"] |= result["compose_changed"]
    result["sockets"] = sockets
    if sockets:
        # Units only while instances are behind the edge; stop removes them.
        if instances:
            result["units_changed"] = write_units(inspect, edge_dir, ipv6, ports)
            result["changed"] |= result["units_changed"]
        result["units_present"] = os.path.exists(os.path.join(units_dir(), SOCKET_UNIT))
        result["sockets_active"] = systemctl(run, "is-active", "--quiet", SOCKET_UNIT)[0] == 0
    if not instances:
        return result

    thumbprint = None
    if any(acme_hostnames(i) for i in instances):
        result["acme_registered"] = not (
            _read(os.path.join(edge_dir, "acme", "thumbprint")) or "").strip()
        thumbprint = ensure_account(run, inspect, edge_dir)
    elif os.path.exists(os.path.join(edge_dir, "acme", "thumbprint")):
        thumbprint = _read(os.path.join(edge_dir, "acme", "thumbprint")).strip()
    # Certificates acme.sh issued earlier may still need removing.
    result["acme"] = bool(thumbprint)

    result["rootless"] = not sockets and daemon_rootless(run, inspect)
    rendered = render(instances, acme_thumbprint=thumbprint,
                      cdn_ranges=cdn_ranges(edge_dir, cdn_providers(instances)),
                      ipv6=ipv6, sockets=sockets, rootless=result["rootless"])
    certs_changed = ensure_certificates(
        run, inspect, edge_dir, rendered["certificates"], instances)
    conf = os.path.join(edge_dir, "conf")
    pending = {n: c for n, c in rendered["files"].items()
               if _read(os.path.join(conf, n)) != c}
    if pending:
        validate(run, inspect, edge_dir, rendered["files"])
        for name, content in pending.items():
            _write_if_changed(os.path.join(conf, name), content)
    # Lists and CrowdSec configurations no instance uses any more. A CDN's
    # list is kept: it is the fallback when the CDN's own cannot be fetched.
    for name in os.listdir(conf):
        stale = (name.endswith(".lst") and not name.startswith("cdn-")) or \
            (name.startswith("spoe-") and name.endswith(".cfg"))
        if stale and name not in rendered["files"]:
            os.remove(os.path.join(conf, name))
    if pending or certs_changed:
        result["changed"] = True
        result["reload"] = True
    return result


def _acme(run, inspect, args, env=None):
    # `-e NAME` takes the value from the exec's own environment, which keeps
    # tokens off the command line, where any user on the host can read it.
    argv = [inspect, "exec"]
    for key in sorted(env or {}):
        argv += ["-e", key]
    return run(argv + [ACME_CONTAINER, "acme.sh"] + args, env=env)


def acme_listed(run, inspect):
    """{hostname: (CA, issued)} for the certificates acme.sh knows. A failed
    first issuance leaves an entry with no creation date."""
    rc, out, err = _acme(run, inspect, ["--list", "--listraw"])
    if rc != 0:
        raise EdgeError("Could not list the edge's certificates: %s"
                        % (err or out).strip())
    listed = {}
    for line in out.splitlines()[1:]:
        fields = line.split("|")
        if len(fields) >= 6 and fields[0]:
            listed[fields[0]] = (fields[4], bool(fields[5].strip()))
    return listed


def _domain_conf(edge_dir, host):
    return _read(os.path.join(edge_dir, "acme", "%s_ecc" % host, "%s.conf" % host)) or ""


def _set_conf(path, key, value):
    """Set (or, with value None, remove) one KEY='value' line of an acme.sh
    conf file, as acme.sh's own _setopt writes them. Returns whether it
    changed."""
    text = _read(path)
    if text is None:
        return False
    lines = [l for l in text.splitlines() if not l.startswith(key + "=")]
    if value is not None:
        lines.append("%s='%s'" % (key, value))
    new = "\n".join(lines) + "\n"
    if new == text:
        return False
    with open(path, "w") as f:
        f.write(new)
    return True


def refresh_dns_credentials(edge_dir, host, dns):
    """Bring the Cloudflare token and zone ID acme.sh saved for host's
    renewals in line with the instance's, so a rotated token reaches them.
    acme.sh keeps them with the certificate when a zone ID is set and for
    the whole account otherwise; the certificate's own copy wins."""
    conf = os.path.join(edge_dir, "acme", "%s_ecc" % host, "%s.conf" % host)
    if dns["zone_id"]:
        changed = _set_conf(conf, "CF_Token", dns["token"])
        return _set_conf(conf, "CF_Zone_ID", dns["zone_id"]) or changed
    changed = _set_conf(conf, "CF_Token", None)
    changed = _set_conf(conf, "CF_Zone_ID", None) or changed
    account = os.path.join(edge_dir, "acme", "account.conf")
    return _set_conf(account, "SAVED_CF_Token", dns["token"]) or changed


def issued_method(edge_dir, host):
    """How acme.sh validates host at renewal: 'stateless' (HTTP-01 answered
    by HAProxy) or a DNS hook such as dns_cf. None when it has no record."""
    for line in _domain_conf(edge_dir, host).splitlines():
        if line.startswith("Le_Webroot="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def _tail(out, err):
    lines = [l for l in (out + "\n" + err).splitlines() if l.strip()]
    return lines[-1] if lines else ""


# acme.sh ends a failed issuance with these, which name no cause.
ACME_CLOSING_LINES = ("Please add '--debug' or '--log'", "See: ",
                      "Please check log file")
ACME_PROBLEM_TYPE = re.compile(r'"type"\s*:\s*"urn:ietf:params:acme:error:([A-Za-z]+)"')
ACME_PROBLEM_DETAIL = re.compile(r'"detail"\s*:\s*"((?:[^"\\]|\\.)*)"')
ACME_TIMESTAMP = re.compile(r"^\[[^\]]*\]\s*")
# What to check for each kind of ACME problem; any other takes the default.
# A rate limit's detail already says when Let's Encrypt accepts requests again.
ACME_PROBLEM_HINTS = {
    "rateLimited": "",
    "caa": "Allow letsencrypt.org in the domain's CAA records.",
    "dns": "Check that %s has a DNS record.",
}
ACME_DEFAULT_HINT = "Check that %s resolves to this host and that port 80 reaches it."
# The problems a first validation can meet while HAProxy takes in the
# challenge rule; acme.sh names none for a response it found wrong.
RETRIED_PROBLEMS = (None, "unauthorized", "incorrectResponse")


def acme_failure(out, err):
    """(problem type, reason) for a failed acme.sh run. The reason is Let's
    Encrypt's own detail when acme.sh printed its response, and otherwise
    acme.sh's last line before its closing pointers to its documentation.
    The problem type is None when the output has none."""
    text = out + "\n" + err
    kind = ACME_PROBLEM_TYPE.search(text)
    detail = ACME_PROBLEM_DETAIL.search(text)
    if detail:
        return (kind.group(1) if kind else None,
                detail.group(1).replace('\\"', '"').strip().rstrip("."))
    lines = [ACME_TIMESTAMP.sub("", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not l.startswith(ACME_CLOSING_LINES)]
    return (kind.group(1) if kind else None,
            lines[-1].rstrip(".") if lines else "")


def acme_hint(kind, host):
    hint = ACME_PROBLEM_HINTS.get(kind, ACME_DEFAULT_HINT)
    return hint % host if "%s" in hint else hint


def issue(run, inspect, edge_dir, retry=False):
    """Obtain, replace and remove certificates to match the members. With
    retry, a stateless issuance whose validation failed is requested once
    more, once HAProxy answers challenges again."""
    return _locked(edge_dir, _issue, run, inspect, edge_dir, retry)


# How long to wait for HAProxy to answer on its stats socket, which
# acme.sh's deploy hook talks to, after the edge has just started.
STATS_SOCKET_WAIT = 60


def stats_socket_ready(run, inspect):
    """Whether HAProxy answers on its stats socket, polled from the acme.sh
    container as its deploy hook reaches it, for up to STATS_SOCKET_WAIT
    seconds."""
    socket = ACME_DEPLOY_ENV["DEPLOY_HAPROXY_STATS_SOCKET"]
    deadline = time.monotonic() + STATS_SOCKET_WAIT
    while True:
        rc, _, _ = run([inspect, "exec", ACME_CONTAINER, "sh", "-c",
                        "echo 'show info' | socat %s - >/dev/null" % shlex.quote(socket)])
        if rc == 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(1)


# How long to wait for HAProxy to answer challenges after a reload that
# brought in the challenge rule.
CHALLENGE_WAIT = 60


def challenge_answered(port, thumbprint):
    """Whether HAProxy answers an HTTP-01 challenge on the host's port with
    the account's thumbprint, as Let's Encrypt will ask it, polled for up to
    CHALLENGE_WAIT seconds. A configuration from before the rule answers with
    its HTTPS redirect instead."""
    probe = "canasta-probe-%d" % int(time.time())
    expected = "%s.%s" % (probe, thumbprint)
    deadline = time.monotonic() + CHALLENGE_WAIT
    while True:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", "/.well-known/acme-challenge/" + probe)
            response = conn.getresponse()
            body = response.read().decode("utf-8", "replace").strip()
            if response.status == 200 and body == expected:
                return True
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()
        if time.monotonic() >= deadline:
            return False
        time.sleep(1)


def _issue(run, inspect, edge_dir, retry=False):
    result = {"changed": False, "reload": False, "issued": [], "unissued": [],
              "unregistered": [], "removed": []}
    instances = _load_members(edge_dir, read_members(edge_dir))
    ready = []
    thumbprint = (_read(os.path.join(edge_dir, "acme", "thumbprint")) or "").strip()
    answering = {}

    def answered(port, again=False):
        """Each failed validation counts against Let's Encrypt's hourly
        limit for the hostname, so none is requested before HAProxy
        answers."""
        if again or port not in answering:
            answering[port] = bool(thumbprint) and challenge_answered(port, thumbprint)
        return answering[port]

    def deploy(host):
        """Install a certificate in HAProxy, which also records HAProxy as
        the destination of its renewals. An edge that has just started
        cannot take it until its stats socket answers."""
        if not ready:
            ready.append(stats_socket_ready(run, inspect))
        if not ready[0]:
            return 1, "", "HAProxy's stats socket did not answer"
        return _acme(run, inspect, ["--deploy", "-d", host, "--ecc",
                                    "--deploy-hook", "haproxy"], env=ACME_DEPLOY_ENV)

    wanted = {}
    for inst in instances:
        for host in acme_hostnames(inst):
            dns = inst["dns"] if host_tls_mode(inst, host) == "acme-dns" else None
            wanted[host] = (inst["staging_certs"], dns, inst["http_port"])
    listed = acme_listed(run, inspect)

    for host in sorted(wanted):
        staging, dns, port = wanted[host]
        method = dns["hook"] if dns else "stateless"
        current = listed.get(host)
        # An unreadable record is left alone rather than spend a reissue.
        if (current and current[1] and current[0] == ACME_CA_NAMES[staging]
                and issued_method(edge_dir, host) in (method, None)):
            if dns and refresh_dns_credentials(edge_dir, host, dns):
                result["changed"] = True
            # A certificate imported from Caddy has not been deployed by
            # acme.sh yet, so its renewals would not reach HAProxy.
            if "Le_DeployHook=" not in _domain_conf(edge_dir, host):
                rc, out, err = deploy(host)
                if rc != 0:
                    result["unregistered"].append({"hostname": host,
                                                   "reason": _tail(out, err)})
            continue
        if dns:
            args = ["--issue", "--dns", dns["hook"], "-d", host]
            # With a zone ID acme.sh keeps the token with this certificate
            # rather than for the whole edge.
            env = {"CF_Token": dns["token"]}
            if dns["zone_id"]:
                env["CF_Zone_ID"] = dns["zone_id"]
        else:
            args = ["--issue", "--stateless", "-d", host]
            env = None
        args += ["--server", ACME_SERVERS[staging]]
        if current:
            args.append("--force")
        if not dns and not answered(port):
            result["unissued"].append({
                "hostname": host, "hint": "",
                "reason": "HAProxy did not answer ACME challenges on port %d, so "
                          "Let's Encrypt was not asked" % port})
            continue
        rc, out, err = _acme(run, inspect, args, env=env)
        if (rc != 0 and not dns and retry
                and acme_failure(out, err)[0] in RETRIED_PROBLEMS and answered(port, True)):
            if "--force" not in args:
                args.append("--force")
            rc, out, err = _acme(run, inspect, args, env=env)
        if rc != 0:
            kind, reason = acme_failure(out, err)
            result["unissued"].append({"hostname": host, "reason": reason,
                                       "hint": acme_hint(kind, host)})
            continue
        rc, out, err = deploy(host)
        if rc != 0:
            result["unissued"].append({"hostname": host, "reason": _tail(out, err),
                                       "hint": ""})
            continue
        result["issued"].append(host)
        result["changed"] = True

    certs = os.path.join(edge_dir, "conf", "certs")
    for host in sorted(set(listed) - set(wanted)):
        _acme(run, inspect, ["--remove", "-d", host, "--ecc"])
        run([inspect, "exec", ACME_CONTAINER, "rm", "-rf", "/acme.sh/%s_ecc" % host])
        result["removed"].append(host)
        result["changed"] = True
        # Still served, but no longer from Let's Encrypt (internal or
        # custom now): its PEM must be replaced, and HAProxy reloaded to
        # read the replacement.
        if os.path.exists(os.path.join(certs, "%s.pem" % host)):
            os.remove(os.path.join(certs, "%s.pem" % host))
            result["reload"] = True
    if result["reload"] and instances:
        ensure_certificates(run, inspect, edge_dir,
                            certificates(instances), instances)
    return result


# Where Caddy keeps the certificates each CA issued, in its data volume.
CADDY_CERT_ROOT = "/data/caddy/certificates"
CADDY_ISSUER_DIRS = {
    False: ("acme-v02.api.letsencrypt.org-directory", "acme.zerossl.com-v2-dv90"),
    True: ("acme-staging-v02.api.letsencrypt.org-directory",),
}
ACME_CA_URLS = {False: "https://acme-v02.api.letsencrypt.org/directory",
                True: "https://acme-staging-v02.api.letsencrypt.org/directory"}
RENEW_BEFORE = 30 * 86400


def _caddy_certificates(run, instance_path, compose_command):
    """{issuer dir: {hostname: (crt, key)}} from the instance's caddy-data
    volume, read through compose so the volume's name need not be known."""
    rc, out, err = run(shlex.split(compose_command) + [
        "run", "--rm", "--no-deps", "--entrypoint", "sh", "caddy", "-c",
        "[ -d %s ] && tar -C %s -cf - . | base64 || true"
        % (CADDY_CERT_ROOT, CADDY_CERT_ROOT)], cwd=instance_path)
    if rc != 0:
        raise EdgeError("Could not read Caddy's certificates: %s" % _tail(out, err))
    found = {}
    data = base64.b64decode("".join(out.split()) or b"")
    if not data:
        return found
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            parts = member.name.lstrip("./").split("/")
            if len(parts) != 3 or not member.isfile():
                continue
            issuer, host, name = parts
            if name not in ("%s.crt" % host, "%s.key" % host):
                continue
            entry = found.setdefault(issuer, {}).setdefault(host, {})
            entry[name.rsplit(".", 1)[1]] = archive.extractfile(member).read().decode()
    return found


def _cert_dates(run, inspect, edge_dir, pem):
    """(notBefore, notAfter) of a certificate, as epoch seconds."""
    rc, out, err = run([inspect, "run", "--rm", "-i", "--entrypoint", "openssl",
                        ACME_IMAGE, "x509", "-noout", "-startdate", "-enddate"],
                       data=pem)
    dates = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if rc != 0 or "notAfter" not in dates:
        raise EdgeError("Could not read a certificate's dates: %s" % _tail(out, err))
    return tuple(calendar.timegm(time.strptime(dates[k], "%b %d %H:%M:%S %Y %Z"))
                 for k in ("notBefore", "notAfter"))


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def import_caddy_certificates(run, inspect, edge_dir, instance_id, instance_path,
                              compose_command):
    """Move the certificates the instance's Caddy holds into the edge.

    Each becomes the edge's PEM for its hostname and an acme.sh record that
    renews it, so the switch to the edge needs no new certificates. Returns
    the hostnames imported."""
    inst = load_instance(instance_id, instance_path)
    if inst is None:
        return []
    hosts = [h for h in acme_hostnames(inst) if host_tls_mode(inst, h) == "acme"]
    if not hosts:
        return []
    held = _caddy_certificates(run, instance_path, compose_command)
    certs = os.path.join(edge_dir, "conf", "certs")
    os.makedirs(certs, mode=0o700, exist_ok=True)
    imported = []
    for host in hosts:
        pair = next((held[d][host] for d in CADDY_ISSUER_DIRS[inst["staging_certs"]]
                     if host in held.get(d, {}) and len(held[d][host]) == 2), None)
        # acme.sh records hold ECDSA P-256 keys, Caddy's default.
        if not pair or "BEGIN EC PRIVATE KEY" not in pair["key"]:
            continue
        chain = pair["crt"]
        end = chain.index("-----END CERTIFICATE-----") + len("-----END CERTIFICATE-----")
        leaf, rest = chain[:end].strip() + "\n", chain[end:].strip() + "\n"
        not_before, not_after = _cert_dates(run, inspect, edge_dir, leaf)
        renew = max(not_after - RENEW_BEFORE, int(time.time()))
        record = os.path.join(edge_dir, "acme", "%s_ecc" % host)
        os.makedirs(record, mode=0o700, exist_ok=True)
        for name, content in (("%s.cer" % host, leaf), ("ca.cer", rest),
                              ("fullchain.cer", chain), ("%s.key" % host, pair["key"])):
            _write_if_changed(os.path.join(record, name), content, 0o600)
        conf = {"Le_Domain": host, "Le_Alt": "no", "Le_Webroot": "stateless",
                "Le_API": ACME_CA_URLS[inst["staging_certs"]], "Le_Keylength": "ec-256",
                "Le_CertCreateTime": str(not_before), "Le_CertCreateTimeStr": _iso(not_before),
                "Le_NextRenewTime": str(renew), "Le_NextRenewTimeStr": _iso(renew)}
        _write_if_changed(os.path.join(record, "%s.conf" % host),
                          "".join("%s='%s'\n" % kv for kv in conf.items()), 0o600)
        pem = chain if chain.endswith("\n") else chain + "\n"
        _write_if_changed(os.path.join(certs, "%s.pem" % host), pem + pair["key"], 0o600)
        imported.append(host)
    return imported


def _edge_view(instance_id, path, env):
    """The renderer's view of an instance as it would be behind the edge;
    raises EdgeError when the edge cannot serve it."""
    wikis = (yaml.safe_load(_read(os.path.join(path, "config", "wikis.yaml")) or "")
             or {}).get("wikis") or []
    media = (yaml.safe_load(_read(os.path.join(path, "config", "media-hosts.yaml")) or "")
             or {}).get("media_hosts") or []
    return instance_from_config(instance_id, env, wikis,
                                [m for m in media if (m.get("hostname") or "").strip()])


def _ineligible(instance_id, path, env):
    """Why an instance cannot move behind the edge, or None if it can."""
    try:
        _edge_view(instance_id, path, env)
    except EdgeError as exc:
        return str(exc)
    return None


def _writable(path):
    env_file = os.path.join(path, ".env")
    return os.access(path, os.W_OK) and (
        not os.path.exists(env_file) or os.access(env_file, os.W_OK))


def daemon_rootless(run, inspect, docker_host=""):
    """Whether the Docker daemon at docker_host runs rootless. Its port
    forwarding then replaces client addresses, as rootless Podman's does."""
    env = {"DOCKER_HOST": docker_host} if docker_host else None
    rc, out, _ = run([inspect, "info", "--format", "{{json .SecurityOptions}}"], env=env)
    return rc == 0 and "name=rootless" in out


def behind_edge(path):
    """Whether an instance is behind the edge already: its edge layer
    exists, which only the edge sync writes."""
    return os.path.exists(os.path.join(path, LAYER_FILE))


def plan_conversion(instances, rootless=lambda inst: False, podman_ready=lambda: True):
    """Decide how a host's Compose instances move behind the edge, which is
    every Compose instance's front door.

    instances: [{id, path, orchestrator, compose_command}]; Kubernetes
    instances are left out. Every instance not yet behind the edge moves.
    If any of them cannot, the host is refused as a whole and none of its
    instances is upgraded, since none could come back without a front door.
    Returns {convert: [ids], rootless: [ids moving on rootless Docker],
    rated: [those of them that set the per-client rates], refuse: reason or
    None, held: [Compose instance ids a refusal holds back]}."""
    compose = [i for i in instances
               if not is_k8s_orchestrator(i.get("orchestrator") or "compose")
               and os.path.isdir(i["path"])]
    names = ", ".join("'%s'" % i["id"] for i in compose)

    def refuse(reason):
        return {"convert": [], "rootless": [], "rated": [], "refuse": reason,
                "held": [i["id"] for i in compose]}

    families = {"podman" if "podman" in (i.get("compose_command") or "") else "docker"
                for i in compose}
    if len(families) > 1:
        return refuse("its instances use both Docker and Podman, which cannot share one "
                      "HAProxy edge (%s). Move one runtime's instances to another host, or "
                      "onto the same runtime, and upgrade again." % names)
    if families == {"docker"} and len({bool(rootless(i)) for i in compose}) > 1:
        return refuse("its instances use both rootful and rootless Docker, which cannot "
                      "share one HAProxy edge (%s). Move one daemon's instances to another "
                      "host, or onto the same daemon, and upgrade again." % names)
    convert, on_rootless, rated, problems = [], [], [], []
    for inst in compose:
        if behind_edge(inst["path"]):
            continue
        env = read_env(inst["path"])
        podman = "podman" in (inst.get("compose_command") or "")
        if not _writable(inst["path"]):
            problems.append("'%s': the CLI cannot write to its directory" % inst["id"])
        elif podman and not podman_ready():
            problems.append("'%s': under rootless Podman the edge needs the operator's systemd "
                            "user session, which is not running; enable lingering for this "
                            "user ('canasta install podman' does)" % inst["id"])
        else:
            reason = _ineligible(inst["id"], inst["path"], env)
            if reason:
                problems.append(reason)
                continue
            found = tls_directives(inst["path"])
            if found:
                problems.append("'%s' %s" % (inst["id"], tls_directives_problem(found)))
                continue
            convert.append(inst["id"])
            # Rootless Podman reports rootless too, but its edge takes its
            # sockets from systemd and keeps client addresses.
            if not podman and rootless(inst):
                on_rootless.append(inst["id"])
                if rates_set(env):
                    rated.append(inst["id"])
    if problems:
        return refuse("the HAProxy edge cannot serve every instance on it yet: %s. Fix "
                      "that and upgrade again." % "; ".join(problems))
    # Each could move on its own; together they must not clash on a port.
    if convert:
        try:
            check_together([_edge_view(i["id"], i["path"], read_env(i["path"]))
                            for i in compose])
        except EdgeError as exc:
            return refuse(str(exc))
    return {"convert": convert, "rootless": on_rootless, "rated": rated, "refuse": None,
            "held": []}


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(type="str",
                       choices=["sync", "start", "stop", "issue", "plan", "import", "prune",
                                "check", "writable"],
                       default="sync"),
            recreate=dict(type="bool", default=False),
            restart=dict(type="bool", default=False),
            retry=dict(type="bool", default=False),
            instances=dict(type="list", elements="dict"),
            compose_command=dict(type="str", default="docker compose"),
            instance_id=dict(type="str"),
            instance_path=dict(type="str"),
            member=dict(type="str", choices=["present", "absent"]),
            inspect_command=dict(type="str", default="docker"),
            edge_dir=dict(type="str"),
        ),
        required_if=[("state", "sync", ("instance_id", "instance_path")),
                     ("state", "import", ("instance_id", "instance_path")),
                     ("state", "check", ("instance_id", "instance_path")),
                     ("state", "plan", ("instances",))],
    )
    params = module.params
    edge_dir = params["edge_dir"] or os.path.join(get_config_dir(), "edge")

    def run(argv, env=None, cwd=None, data=None):
        return module.run_command(argv, environ_update=env or {}, cwd=cwd, data=data)

    try:
        if params["state"] == "start":
            start_edge(run, params["inspect_command"], params["compose_command"], edge_dir,
                       params["recreate"], params["restart"])
            result = {"changed": True}
        elif params["state"] == "stop":
            stop_edge(run, params["inspect_command"], params["compose_command"], edge_dir)
            result = {"changed": True}
        elif params["state"] == "issue":
            result = issue(run, params["inspect_command"], edge_dir, params["retry"])
        elif params["state"] == "plan":
            seen = {}

            def rootless(inst):
                host = inst.get("docker_host") or ""
                if host not in seen:
                    seen[host] = daemon_rootless(run, params["inspect_command"], host)
                return seen[host]
            podman = None

            def podman_ready():
                nonlocal podman
                if podman is None:
                    podman = user_systemd_available(run)
                return podman
            result = dict(plan_conversion(params["instances"] or [], rootless, podman_ready),
                          changed=False)
        elif params["state"] == "check":
            found = tls_directives(params["instance_path"])
            if found:
                problem = tls_directives_problem(found)
                module.fail_json(msg="Instance '%s' %s." % (params["instance_id"], problem),
                                 reason="it %s." % problem, tls_directives=found)
                return
            result = {"changed": False, "tls_directives": []}
        elif params["state"] == "writable":
            check_writable(edge_dir, params["instance_path"])
            result = {"changed": False}
        elif params["state"] == "prune":
            removed = prune_spoa_volumes(run, params["inspect_command"])
            result = {"changed": bool(removed), "removed": removed}
        elif params["state"] == "import":
            imported = _locked(edge_dir, import_caddy_certificates, run,
                               params["inspect_command"], edge_dir, params["instance_id"],
                               params["instance_path"], params["compose_command"])
            result = {"changed": bool(imported), "imported": imported}
        else:
            result = sync(run, params["inspect_command"], edge_dir,
                          params["instance_id"], params["instance_path"],
                          params["member"])
    except EdgeError as exc:
        module.fail_json(msg=str(exc))
        return
    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
