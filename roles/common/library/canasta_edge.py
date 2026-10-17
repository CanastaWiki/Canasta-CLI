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

An instance is behind the edge when its .env sets CANASTA_ENABLE_EDGE=true.
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
      sync renders and stages the edge. issue obtains, renews and removes
      Let's Encrypt certificates through acme.sh, once the edge is running
      and answering challenges.
    type: str
    choices: [sync, issue]
    default: sync
  instance_id:
    description: The instance's registry ID (required for sync).
    type: str
  instance_path:
    description: Path to the instance directory (required for sync).
    type: str
  member:
    description: >-
      present adds the instance to the edge, absent removes it (and its
      layer). Omit to follow CANASTA_ENABLE_EDGE in the instance's .env.
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
      orchestrator and compose_command.
    type: list
    elements: dict
  compose_command:
    description: >-
      With state=import, the compose command that reads the instance's
      caddy-data volume.
    type: str
    default: docker compose
"""

import base64
import calendar
import fcntl
import io
import ipaddress
import json
import os
import shlex
import shutil
import tarfile
import tempfile
import time

import yaml

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_config import get_config_dir, is_k8s_orchestrator
from ansible.module_utils.canasta_edge_render import (
    ACME_IMAGE, CERT_DIR, CONFIG_DIR, DENY_CIDR_FILE, DENY_UA_FILE, EdgeError,
    HAPROXY_IMAGE, cdn_providers, certificates, instance_from_config, render,
)
from ansible.module_utils.canasta_caddyfile import site_hostnames
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
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
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
networks:
  %(network)s:
    external: true
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
    return env


def edge_enabled(env):
    return (env.get("CANASTA_ENABLE_EDGE") or "false").strip().lower() == "true"


def write_layer(instance_path, instance_id, present):
    """Write or remove the instance's edge layer. Returns True if changed."""
    path = os.path.join(instance_path, LAYER_FILE)
    if not present:
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
    return _write_if_changed(path, LAYER % {
        "network": NETWORK, "id": instance_id, "path": instance_path,
        "label_id": LABEL_ID, "label_path": LABEL_PATH,
    })


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


def load_instance(instance_id, path):
    """The renderer's view of an instance, or None when it is not (or no
    longer) behind the edge."""
    env = read_env(path)
    if not os.path.isdir(path) or not edge_enabled(env):
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


def helper_user(run, inspect, edge_dir):
    """The user the edge's helper containers (openssl, acme.sh) run as.

    The edge directory belongs to whoever runs canasta. Where container
    root is host root (rootful Docker or Podman) and that is not root, a
    helper running as root would leave root-owned files and directories
    the operator cannot remove, so helpers run as the operator. Elsewhere
    (rootless runtimes, or canasta run as root) root already maps to the
    operator. Decided once by having a container create a file, and kept.
    """
    cached = os.path.join(edge_dir, ".helper-user")
    user = (_read(cached) or "").strip()
    if user:
        return user
    user = "root"
    if os.getuid() != 0:
        probe = os.path.join(edge_dir, ".owner-probe")
        run([inspect, "run", "--rm", "-v", "%s:/probe" % edge_dir,
             "--entrypoint", "touch", ACME_IMAGE, "/probe/.owner-probe"])
        if os.path.exists(probe):
            if os.stat(probe).st_uid == 0:
                user = "%d:%d" % (os.getuid(), os.getgid())
            run([inspect, "run", "--rm", "-v", "%s:/probe" % edge_dir,
                 "--entrypoint", "rm", ACME_IMAGE, "-f", "/probe/.owner-probe"])
    _write_if_changed(cached, user + "\n")
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
                "Instance '%s': CADDY_TLS_MODE is 'custom' but %s or %s is "
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
    stage = tempfile.mkdtemp(prefix=".stage-", dir=edge_dir)
    try:
        for name, content in files.items():
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
        member = "present" if edge_enabled(env) else "absent"
    result["changed"] |= write_layer(instance_path, instance_id, member == "present")

    if member == "absent" and not os.path.isdir(edge_dir):
        result.update(members=[], running=False)
        return result

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
    ipv6 = host_has_ipv6()
    result["acme_running"] = edge_running(run, inspect, ACME_CONTAINER)

    result["compose_changed"] = _write_if_changed(
        os.path.join(edge_dir, "docker-compose.yml"),
        EDGE_COMPOSE % {"image": HAPROXY_IMAGE, "container": CONTAINER,
                        "config_dir": CONFIG_DIR, "network": NETWORK,
                        "acme_image": ACME_IMAGE, "acme_container": ACME_CONTAINER,
                        "acme_user": helper_user(run, inspect, edge_dir),
                        "cert_dir": CERT_DIR,
                        "ipv6": IPV6_NETWORK if ipv6 else ""})
    result["changed"] |= result["compose_changed"]
    if not instances:
        return result

    thumbprint = None
    if any(i["tls_mode"] in ("acme", "acme-dns") for i in instances):
        thumbprint = ensure_account(run, inspect, edge_dir)
    elif os.path.exists(os.path.join(edge_dir, "acme", "thumbprint")):
        thumbprint = _read(os.path.join(edge_dir, "acme", "thumbprint")).strip()
    # Certificates acme.sh issued earlier may still need removing.
    result["acme"] = bool(thumbprint)

    rendered = render(instances, acme_thumbprint=thumbprint,
                      cdn_ranges=cdn_ranges(edge_dir, cdn_providers(instances)),
                      ipv6=ipv6)
    certs_changed = ensure_certificates(
        run, inspect, edge_dir, rendered["certificates"], instances)
    conf = os.path.join(edge_dir, "conf")
    pending = {n: c for n, c in rendered["files"].items()
               if _read(os.path.join(conf, n)) != c}
    if pending:
        validate(run, inspect, edge_dir, rendered["files"])
        for name, content in pending.items():
            _write_if_changed(os.path.join(conf, name), content)
    # Lists no instance uses any more. A CDN's is kept: it is the fallback
    # when the CDN's own list cannot be fetched.
    for name in os.listdir(conf):
        if (name.endswith(".lst") and name not in rendered["files"]
                and not name.startswith("cdn-")):
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


def issue(run, inspect, edge_dir):
    """Obtain, replace and remove certificates to match the members."""
    return _locked(edge_dir, _issue, run, inspect, edge_dir)


def _issue(run, inspect, edge_dir):
    result = {"changed": False, "reload": False, "issued": [], "unissued": [],
              "removed": []}
    instances = _load_members(edge_dir, read_members(edge_dir))
    wanted = {}
    for inst in instances:
        if inst["tls_mode"] in ("acme", "acme-dns"):
            for host in inst["hostnames"] + inst["media_hostnames"]:
                wanted[host] = (inst["staging_certs"], inst["dns"])
    listed = acme_listed(run, inspect)

    for host in sorted(wanted):
        staging, dns = wanted[host]
        method = dns["hook"] if dns else "stateless"
        current = listed.get(host)
        # An unreadable record is left alone rather than spend a reissue.
        if (current and current[1] and current[0] == ACME_CA_NAMES[staging]
                and issued_method(edge_dir, host) in (method, None)):
            # A certificate imported from Caddy has not been deployed by
            # acme.sh yet, so its renewals would not reach HAProxy.
            if "Le_DeployHook=" not in _domain_conf(edge_dir, host):
                rc, out, err = _acme(run, inspect, ["--deploy", "-d", host, "--ecc",
                                                    "--deploy-hook", "haproxy"],
                                     env=ACME_DEPLOY_ENV)
                if rc != 0:
                    result["unissued"].append({"hostname": host, "reason": _tail(out, err)})
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
        rc, out, err = _acme(run, inspect, args, env=env)
        if rc != 0:
            result["unissued"].append({"hostname": host, "reason": _tail(out, err)})
            continue
        rc, out, err = _acme(run, inspect, ["--deploy", "-d", host, "--ecc",
                                            "--deploy-hook", "haproxy"],
                             env=ACME_DEPLOY_ENV)
        if rc != 0:
            result["unissued"].append({"hostname": host, "reason": _tail(out, err)})
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
    if inst is None or inst["tls_mode"] != "acme":
        return []
    held = _caddy_certificates(run, instance_path, compose_command)
    certs = os.path.join(edge_dir, "conf", "certs")
    os.makedirs(certs, mode=0o700, exist_ok=True)
    imported = []
    for host in inst["hostnames"] + inst["media_hostnames"]:
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


def _ineligible(instance_id, path, env):
    """Why an instance cannot move behind the edge, or None if it can."""
    if (env.get("CADDY_AUTO_HTTPS") or "on").strip().lower() == "off":
        return "Caddy sits behind another proxy (CADDY_AUTO_HTTPS=off)"
    if (env.get("HTTP_PORT") or "80").strip() != "80" or \
            (env.get("HTTPS_PORT") or "443").strip() != "443":
        return "it serves its own ports (HTTP_PORT/HTTPS_PORT)"
    try:
        probe = dict(env, CANASTA_ENABLE_EDGE="true")
        wikis = (yaml.safe_load(_read(os.path.join(path, "config", "wikis.yaml")) or "")
                 or {}).get("wikis") or []
        media = (yaml.safe_load(_read(os.path.join(path, "config", "media-hosts.yaml")) or "")
                 or {}).get("media_hosts") or []
        instance_from_config(instance_id, probe, wikis,
                             [m for m in media if (m.get("hostname") or "").strip()])
    except EdgeError as exc:
        return str(exc)
    return None


def _writable(path):
    env_file = os.path.join(path, ".env")
    return os.access(path, os.W_OK) and (
        not os.path.exists(env_file) or os.access(env_file, os.W_OK))


def _publishes_web_ports(env):
    if edge_enabled(env):
        return False
    https = (env.get("CADDY_AUTO_HTTPS") or "on").strip().lower() != "off"
    return ((env.get("HTTP_PORT") or "80").strip() == "80"
            or (https and (env.get("HTTPS_PORT") or "443").strip() == "443"))


def plan_conversion(instances):
    """Decide which of a host's Compose instances move behind the edge.

    instances: [{id, path, orchestrator, compose_command}]; Kubernetes
    instances are left out. An instance whose .env already
    sets CANASTA_ENABLE_EDGE keeps its choice. One that can move does, unless
    an instance staying outside the edge serves 80 or 443 itself, which the
    edge would collide with; then none on the host move. One whose files the
    CLI cannot write is skipped without recording a choice, so the next
    upgrade considers it again. Returns {convert: [ids], decline: {id:
    reason}, skip: {id: reason}}."""
    convert, decline, skip, staying = [], {}, {}, []
    runtimes = set()
    for inst in instances:
        if is_k8s_orchestrator(inst.get("orchestrator") or "compose") or \
                not os.path.isdir(inst["path"]):
            continue
        env = read_env(inst["path"])
        if (env.get("CANASTA_ENABLE_EDGE") or "").strip():
            if not edge_enabled(env):
                staying.append((inst["id"], env))
            continue
        if not _writable(inst["path"]):
            skip[inst["id"]] = "the CLI cannot write to its directory"
            staying.append((inst["id"], env))
            continue
        reason = _ineligible(inst["id"], inst["path"], env)
        if reason:
            decline[inst["id"]] = reason
            staying.append((inst["id"], env))
        else:
            convert.append(inst["id"])
            runtimes.add(inst.get("compose_command") or "")
    blockers = [i for i, env in staying if _publishes_web_ports(env)]
    if convert and blockers:
        reason = ("instance %s on this host serves ports 80/443 itself, and the "
                  "edge would need them" % ", ".join("'%s'" % b for b in blockers))
        decline.update({i: reason for i in convert})
        convert = []
    if len(runtimes) > 1:
        reason = "the host's instances use different container runtimes"
        decline.update({i: reason for i in convert})
        convert = []
    return {"convert": convert, "decline": decline, "skip": skip}


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(type="str", choices=["sync", "issue", "plan", "import"],
                       default="sync"),
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
                     ("state", "plan", ("instances",))],
    )
    params = module.params
    edge_dir = params["edge_dir"] or os.path.join(get_config_dir(), "edge")

    def run(argv, env=None, cwd=None, data=None):
        return module.run_command(argv, environ_update=env or {}, cwd=cwd, data=data)

    try:
        if params["state"] == "issue":
            result = issue(run, params["inspect_command"], edge_dir)
        elif params["state"] == "plan":
            result = dict(plan_conversion(params["instances"] or []), changed=False)
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
