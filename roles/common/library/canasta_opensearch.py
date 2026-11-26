#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module: keep the host's OpenSearch cluster in line with its instances.

The cluster lives in <config dir>/opensearch. Everything in it is generated
from the instances that use it, so it can be deleted and rebuilt at any time:

  docker-compose.yml   the cluster's own Compose project (canasta-opensearch)
  members.yaml         instances with OpenSearch on, and the tokens of removed
                       ones whose users and indexes are still to be deleted
  ca.pem, ca.key       the CA for the cluster's HTTPS and transport layers
  node.pem, node.key   the node's certificate
  admin.pem, admin.key the certificate Canasta administers the cluster with
  security/            the security index's starting point

The operator may add docker-compose.override.yml, which is included and never
written. The indexes are in the canasta-opensearch-data volume, outside this
directory, and survive its loss: the CLI makes new certificates, and the
instances' users are added again from their .env files.

Each instance with OpenSearch on gets a user named after its
CANASTA_OPENSEARCH_TOKEN, with the password CANASTA_OPENSEARCH_PASSWORD and
rights on the <token>_* indexes only, and three files: the
docker-compose.opensearch.yml layer, which joins its web container to the
cluster's network, and the global and per-wiki CirrusSearch settings.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_opensearch
short_description: Run the host's OpenSearch cluster for Compose instances
description:
  - Writes or removes the instance's OpenSearch layer and CirrusSearch settings.
  - Records the instance's membership, starts the cluster or brings it in line,
    and adds, updates or removes the instances' users.
  - Stops the cluster when no instance uses it any more.
options:
  instance_id:
    description: The instance's registry ID.
    type: str
    required: true
  instance_path:
    description: Path to the instance directory.
    type: str
    required: true
  member:
    description: >-
      present when the instance has OpenSearch on, absent when it is off or
      the instance is being deleted.
    type: str
    choices: [present, absent]
    default: present
  compose_command:
    description: The compose command that runs the cluster's project.
    type: str
    default: docker compose
  inspect_command:
    description: The container CLI (docker or podman).
    type: str
    default: docker
  opensearch_dir:
    description: Override the cluster directory (default <config dir>/opensearch).
    type: str
"""

import fcntl
import json
import os
import shlex
import shutil
import tempfile
import time
import hashlib

import yaml

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_config import get_config_dir
from ansible.module_utils.canasta_edge_render import ACME_IMAGE
from ansible.module_utils.canasta_opensearch_render import (
    ADMIN_CN, CONF_DIR, CONF_VOLUME, CONTAINER, GLOBAL_SETTINGS, IMAGE,
    LAYER_FILE, NODE_CN, PROJECT, SECURITY_DIR, WIKI_SETTINGS, heap_mb,
    index_base_name, instance_role, render_compose, render_global_settings,
    render_internal_users, render_layer, render_roles_mapping,
    render_wiki_settings,
)

CERT_FILES = ("ca.pem", "node.pem", "node.key", "admin.pem", "admin.key")
SECURITY_FILES = ("internal_users.yml", "roles_mapping.yml")
CERT_DAYS = 3650
READY_TIMEOUT = 180
OVERRIDE_FILE = "docker-compose.override.yml"
# Marks a user, role and mapping as Canasta's, so ones it no longer needs
# can be told from any an operator added.
INSTANCE_ATTRIBUTE = "canasta_instance"


class OpenSearchError(Exception):
    pass


def _read(path):
    try:
        with open(path) as handle:
            return handle.read()
    except (IOError, OSError):
        return None


def _write_if_changed(path, content, mode=0o644):
    if _read(path) == content:
        return False
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    os.chmod(tmp, mode)
    os.rename(tmp, path)
    return True


def _remove(path):
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def _tail(out, err, lines=8):
    text = "\n".join(t.strip() for t in (out, err) if t and t.strip())
    return "\n".join(text.splitlines()[-lines:]) or "no output"


def read_env(instance_path):
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


def enabled(env):
    return env.get("CANASTA_ENABLE_OPENSEARCH", "").lower() == "true"


def wiki_ids(instance_path):
    data = yaml.safe_load(_read(os.path.join(instance_path, "config", "wikis.yaml")) or "")
    wikis = (data or {}).get("wikis") or []
    return [str(w["id"]) for w in wikis if isinstance(w, dict) and w.get("id")]


# --- Membership -----------------------------------------------------------

def read_members(os_dir):
    data = yaml.safe_load(_read(os.path.join(os_dir, "members.yaml")) or "") or {}
    members = {m["id"]: m["path"] for m in data.get("members") or []
               if m.get("id") and m.get("path")}
    return members, list(data.get("removed") or [])


def write_members(os_dir, members, removed):
    content = yaml.safe_dump(
        {"members": [{"id": i, "path": members[i]} for i in sorted(members)],
         "removed": sorted(set(removed))},
        default_flow_style=False)
    return _write_if_changed(os.path.join(os_dir, "members.yaml"), content)


def load_members(members):
    """{id: {path, token, password, heap}} for members whose OpenSearch is on."""
    loaded = {}
    for inst_id in sorted(members):
        path = members[inst_id]
        if not os.path.isdir(path):
            continue
        env = read_env(path)
        if not enabled(env):
            continue
        loaded[inst_id] = {
            "path": path,
            "token": env.get("CANASTA_OPENSEARCH_TOKEN", ""),
            "password": env.get("CANASTA_OPENSEARCH_PASSWORD", ""),
            "heap": env.get("CANASTA_OPENSEARCH_HEAP", ""),
        }
    return loaded


# --- Instance files -------------------------------------------------------

def indexed_before(instance_path, wiki_id):
    """Whether the wiki's settings file already made CirrusSearch its engine."""
    return "$wgSearchType" in (_read(os.path.join(
        instance_path, WIKI_SETTINGS % wiki_id)) or "")


def write_instance_files(instance_path, os_dir, token, indexed):
    """Write the instance's layer and CirrusSearch settings. indexed maps
    each wiki ID to whether its index is built. Returns True if changed."""
    changed = _write_if_changed(os.path.join(instance_path, LAYER_FILE),
                                render_layer(os.path.join(os_dir, "ca.pem")))
    changed |= _write_if_changed(os.path.join(instance_path, GLOBAL_SETTINGS),
                                 render_global_settings(token))
    for wiki_id, built in indexed.items():
        changed |= _write_if_changed(os.path.join(instance_path, WIKI_SETTINGS % wiki_id),
                                     render_wiki_settings(token, wiki_id, built))
    return changed


def remove_instance_files(instance_path):
    changed = _remove(os.path.join(instance_path, LAYER_FILE))
    changed |= _remove(os.path.join(instance_path, GLOBAL_SETTINGS))
    wikis_dir = os.path.join(instance_path, "config", "settings", "wikis")
    for name in os.listdir(wikis_dir) if os.path.isdir(wikis_dir) else []:
        changed |= _remove(os.path.join(instance_path, WIKI_SETTINGS % name))
    return changed


# --- Containers -----------------------------------------------------------

def ensure_image(run, inspect, image):
    if run([inspect, "image", "inspect", image])[0] == 0:
        return
    rc, out, err = run([inspect, "pull", "-q", image])
    if rc != 0:
        raise OpenSearchError("Could not pull %s: %s" % (image, _tail(out, err)))


def helper_user(run, inspect, os_dir):
    """The user the openssl helper runs as, so the files it writes belong to
    the operator: the operator where container root is host root and the
    operator is not root, root elsewhere (rootless runtimes, or canasta run
    as root). Decided by having a container create a file, and kept with
    the daemon's ID and the uid it was decided for."""
    cached = os.path.join(os_dir, ".helper-user")
    cached_daemon = os.path.join(os_dir, ".helper-user-daemon")
    rc, out, _ = run([inspect, "info", "--format", "{{.ID}}"])
    key = "%s\n%d" % (out.strip() if rc == 0 else "", os.getuid())
    user = (_read(cached) or "").strip()
    if user and (_read(cached_daemon) or "").strip() == key:
        return user
    user = "root"
    if os.getuid() != 0:
        probe = os.path.join(os_dir, ".owner-probe")
        ensure_image(run, inspect, ACME_IMAGE)
        run([inspect, "run", "--rm", "-v", "%s:/probe" % os_dir,
             "--entrypoint", "touch", ACME_IMAGE, "/probe/.owner-probe"])
        if os.path.exists(probe):
            if os.stat(probe).st_uid == 0:
                user = "%d:%d" % (os.getuid(), os.getgid())
            run([inspect, "run", "--rm", "-v", "%s:/probe" % os_dir,
                 "--entrypoint", "rm", ACME_IMAGE, "-f", "/probe/.owner-probe"])
    _write_if_changed(cached, user + "\n")
    _write_if_changed(cached_daemon, key + "\n")
    return user


def _openssl_script(new_ca):
    """Shell for the openssl container: the CA when new_ca, then the node's
    and the admin's certificates, with PKCS#8 keys as OpenSearch reads them."""
    lines = ["set -e", "cd /work"]
    if new_ca:
        lines.append(
            "openssl req -x509 -newkey rsa:3072 -nodes -days %d "
            "-subj '/CN=Canasta OpenSearch CA' -keyout /out/ca.key -out /out/ca.pem "
            "2>/dev/null" % CERT_DAYS)
    for name, cn, san in (("node", NODE_CN, "DNS:%s,DNS:localhost" % NODE_CN),
                          ("admin", ADMIN_CN, None)):
        lines += [
            "openssl req -newkey rsa:3072 -nodes -subj '/CN=%s' "
            "-keyout raw.key -out req.csr 2>/dev/null" % cn,
            "printf '%s' > ext.cnf" % (
                ("subjectAltName=%s\\n" % san if san else "")
                + "extendedKeyUsage=serverAuth,clientAuth\\n"),
            "openssl x509 -req -in req.csr -CA /out/ca.pem -CAkey /out/ca.key "
            "-CAcreateserial -days %d -extfile ext.cnf -out /out/%s.pem 2>/dev/null"
            % (CERT_DAYS, name),
            "openssl pkcs8 -topk8 -nocrypt -in raw.key -out /out/%s.key" % name,
            "chmod 600 /out/%s.key" % name,
            "rm -f raw.key req.csr ext.cnf",
        ]
    lines.append("chmod 600 /out/ca.key; rm -f /out/ca.srl")
    return "\n".join(lines)


def ensure_certificates(run, inspect, os_dir):
    """Create the CA and certificates when any is missing. A new CA gets new
    certificates. Returns True if any was created."""
    missing = [f for f in CERT_FILES + ("ca.key",)
               if not os.path.exists(os.path.join(os_dir, f))]
    if not missing:
        return False
    new_ca = "ca.pem" in missing or "ca.key" in missing
    ensure_image(run, inspect, ACME_IMAGE)
    work = tempfile.mkdtemp(prefix=".work-", dir=os_dir)
    try:
        rc, _, err = run([
            inspect, "run", "--rm", "--user", helper_user(run, inspect, os_dir),
            "-v", "%s:/out" % os_dir, "-v", "%s:/work" % work,
            "--entrypoint", "sh", ACME_IMAGE, "-c", _openssl_script(new_ca)])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if rc != 0:
        raise OpenSearchError("Could not create the OpenSearch certificates: %s"
                              % err.strip())
    return True


def conf_fingerprint(os_dir):
    digest = hashlib.sha256()
    for name in CERT_FILES + tuple("security/" + f for f in SECURITY_FILES):
        digest.update(name.encode())
        digest.update((_read(os.path.join(os_dir, name)) or "").encode())
    return digest.hexdigest()


def volume_exists(run, inspect, name):
    return run([inspect, "volume", "inspect", name])[0] == 0


def install_conf(run, inspect, os_dir):
    """Copy the certificates and security files into the config volume, owned
    by the OpenSearch user (uid 1000), whatever user owns the cluster
    directory. The image's own security files fill in the rest, except its
    demo users and mappings. Returns True if the volume changed."""
    stamp = os.path.join(os_dir, ".conf-installed")
    fingerprint = conf_fingerprint(os_dir)
    if (_read(stamp) or "").strip() == fingerprint and volume_exists(run, inspect, CONF_VOLUME):
        return False
    script = "\n".join([
        "set -e",
        "mkdir -p /c/security",
        "cp %s /c/" % " ".join("/src/%s" % f for f in CERT_FILES),
        "for f in /usr/share/opensearch/config/opensearch-security/*.yml; do",
        "  case \"${f##*/}\" in %s) ;; *) cp \"$f\" /c/security/ ;; esac" % "|".join(SECURITY_FILES),
        "done",
        "cp %s /c/security/" % " ".join("/src/security/%s" % f for f in SECURITY_FILES),
        "chown -R 1000:1000 /c",
        "chmod 700 /c /c/security",
        "chmod 600 /c/*.key",
    ])
    ensure_image(run, inspect, IMAGE)
    rc, out, err = run([inspect, "run", "--rm", "--user", "0",
                        "-v", "%s:/src:ro" % os_dir, "-v", "%s:/c" % CONF_VOLUME,
                        "--entrypoint", "bash", IMAGE, "-c", script])
    if rc != 0:
        raise OpenSearchError("Could not install the OpenSearch configuration: %s"
                              % _tail(out, err))
    _write_if_changed(stamp, fingerprint + "\n", 0o600)
    return True


def running(run, inspect):
    rc, out, _ = run([inspect, "ps", "-q", "--filter", "name=^%s$" % CONTAINER])
    return rc == 0 and bool(out.strip())


def _compose(compose_command, os_dir, *args):
    argv = shlex.split(compose_command) + ["-p", PROJECT, "-f", "docker-compose.yml"]
    if os.path.exists(os.path.join(os_dir, OVERRIDE_FILE)):
        argv += ["-f", OVERRIDE_FILE]
    return argv + list(args)


def start_cluster(run, compose_command, os_dir, recreate):
    args = ["up", "-d"] + (["--force-recreate"] if recreate else [])
    if os.path.exists(os.path.join(os_dir, OVERRIDE_FILE)):
        # An image the override builds is rebuilt, from the build cache
        # when nothing in it changed.
        args.append("--build")
    rc, out, err = run(_compose(compose_command, os_dir, *args), cwd=os_dir)
    if rc != 0:
        raise OpenSearchError("Could not start OpenSearch: %s" % _tail(out, err))


def stop_cluster(run, compose_command, os_dir):
    """Stop the cluster, keeping its volumes for the next instance."""
    if not os.path.exists(os.path.join(os_dir, "docker-compose.yml")):
        return
    rc, out, err = run(_compose(compose_command, os_dir, "down"), cwd=os_dir)
    if rc != 0:
        raise OpenSearchError("Could not stop OpenSearch: %s" % _tail(out, err))


def api(run, inspect, method, path, body=None):
    """Call the cluster's REST API as Canasta's administrator, with the admin
    certificate. Returns (status, parsed JSON or text). The request goes
    through the container, so nothing is published on the host; a body is
    sent on stdin, keeping passwords out of process listings."""
    argv = [inspect, "exec", "-i", CONTAINER, "curl", "-s", "-o", "-",
            "-w", "\n%{http_code}", "--cacert", CONF_DIR + "/ca.pem",
            "--cert", CONF_DIR + "/admin.pem", "--key", CONF_DIR + "/admin.key",
            "-X", method, "-H", "Content-Type: application/json",
            "https://localhost:9200" + path]
    data = None
    if body is not None:
        argv += ["--data-binary", "@-"]
        data = json.dumps(body)
    rc, out, err = run(argv, data=data)
    if rc != 0:
        return 0, _tail(out, err)
    text, _, code = out.rpartition("\n")
    try:
        parsed = json.loads(text) if text.strip() else None
    except ValueError:
        parsed = text
    return int(code) if code.strip().isdigit() else 0, parsed


def wait_ready(run, inspect, timeout=READY_TIMEOUT):
    """Wait for the node to answer. Returns whether its security index exists."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, body = api(run, inspect, "GET", "/_plugins/_security/authinfo")
        if code == 200 and isinstance(body, dict):
            return True
        if code and "Security not initialized" in json.dumps(body):
            # The node answers, but security has not been set up yet.
            return False
        last = body
        time.sleep(3)
    raise OpenSearchError("OpenSearch did not start answering within %d seconds: %s"
                          % (timeout, last))


def init_security(run, inspect):
    """Create the security index from the config volume's files."""
    rc, out, err = run([
        inspect, "exec", "-e", "JAVA_HOME=/usr/share/opensearch/jdk", CONTAINER, "bash",
        "/usr/share/opensearch/plugins/opensearch-security/tools/securityadmin.sh",
        "-cd", SECURITY_DIR, "-icl", "-nhnv", "-h", "localhost",
        "-cacert", CONF_DIR + "/ca.pem", "-cert", CONF_DIR + "/admin.pem",
        "-key", CONF_DIR + "/admin.key"])
    if rc != 0 or "Done with success" not in out:
        raise OpenSearchError("Could not set up OpenSearch security: %s" % _tail(out, err))


def _check(code, body, what):
    if code not in (200, 201):
        raise OpenSearchError("Could not %s: HTTP %s %s" % (what, code, body))


def reconcile_users(run, inspect, members, removed):
    """Give each member its user, role and mapping, and delete Canasta's
    users, roles, mappings and indexes no member uses any more. Returns the
    tokens deleted."""
    security = "/_plugins/_security/api"
    for inst_id, m in sorted(members.items()):
        token = m["token"]
        _check(*api(run, inspect, "PUT", "%s/internalusers/%s" % (security, token), {
            "password": m["password"],
            "description": "Canasta instance %s" % inst_id,
            "attributes": {INSTANCE_ATTRIBUTE: inst_id},
        }), what="set up the OpenSearch user for instance '%s'" % inst_id)
        role = dict(instance_role(token))
        _check(*api(run, inspect, "PUT", "%s/roles/%s" % (security, token), role),
               what="set up the OpenSearch role for instance '%s'" % inst_id)
        _check(*api(run, inspect, "PUT", "%s/rolesmapping/%s" % (security, token),
                    {"users": [token], "description": "Canasta instance %s" % inst_id}),
               what="map the OpenSearch role for instance '%s'" % inst_id)

    code, users = api(run, inspect, "GET", "%s/internalusers" % security)
    _check(code, users, "list the OpenSearch users")
    tokens = {m["token"] for m in members.values()}
    stale = {name for name, user in (users or {}).items()
             if isinstance(user, dict)
             and INSTANCE_ATTRIBUTE in (user.get("attributes") or {})
             and name not in tokens}
    stale |= {t for t in removed if t and t not in tokens}
    for token in sorted(stale):
        for kind in ("internalusers", "roles", "rolesmapping"):
            code, body = api(run, inspect, "DELETE", "%s/%s/%s" % (security, kind, token))
            if code not in (200, 404):
                raise OpenSearchError("Could not delete OpenSearch %s %s: HTTP %s %s"
                                      % (kind, token, code, body))
        delete_indexes(run, inspect, token)
    return sorted(stale)


def delete_indexes(run, inspect, token):
    code, body = api(run, inspect, "GET", "/_cat/indices/%s_*?h=index&format=json" % token)
    if code == 404:
        return
    _check(code, body, "list the indexes of token %s" % token)
    names = [i["index"] for i in body or [] if i.get("index", "").startswith(token + "_")]
    if names:
        _check(*api(run, inspect, "DELETE", "/" + ",".join(names)),
               what="delete the indexes of token %s" % token)


def indexed_wikis(run, inspect, token, wikis):
    """{wiki ID: whether its content index exists and has documents}."""
    result = {}
    for wiki_id in wikis:
        code, body = api(run, inspect, "GET", "/%s_content/_count" % index_base_name(token, wiki_id))
        result[wiki_id] = code == 200 and isinstance(body, dict) and body.get("count", 0) > 0
    return result


# --- Sync -----------------------------------------------------------------

def _locked(os_dir, func, *args):
    """Run func holding the cluster's lock: instances share the cluster, and
    commands for two of them can run at once."""
    os.makedirs(os.path.dirname(os_dir) or ".", exist_ok=True)
    with open(os_dir + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return func(*args)


def sync(run, inspect, compose_command, os_dir, instance_id, instance_path, member):
    return _locked(os_dir, _sync, run, inspect, compose_command, os_dir,
                   instance_id, instance_path, member)


def _sync(run, inspect, compose_command, os_dir, instance_id, instance_path, member):
    result = {"changed": False, "opensearch_dir": os_dir, "members": [],
              "running": False, "removed_tokens": []}
    env = read_env(instance_path)
    present = member == "present" and enabled(env)
    token = env.get("CANASTA_OPENSEARCH_TOKEN", "")
    if present and not (token and env.get("CANASTA_OPENSEARCH_PASSWORD")):
        raise OpenSearchError(
            "Instance '%s' has CANASTA_ENABLE_OPENSEARCH=true but no "
            "CANASTA_OPENSEARCH_TOKEN or CANASTA_OPENSEARCH_PASSWORD." % instance_id)

    if not present:
        result["changed"] |= remove_instance_files(instance_path)
        if not os.path.isdir(os_dir):
            return result

    os.makedirs(os_dir, mode=0o700, exist_ok=True)
    os.chmod(os_dir, 0o700)
    stored, removed = read_members(os_dir)
    stored.pop(instance_id, None)
    if present:
        stored[instance_id] = instance_path
    elif token:
        removed.append(token)
    members = load_members(stored)

    clash = [i for i, m in members.items()
             if i != instance_id and present and m["token"] == token]
    if clash:
        result["token_conflict"] = clash[0]
        return result

    result["changed"] |= write_members(os_dir, {i: m["path"] for i, m in members.items()},
                                       removed)
    result["members"] = sorted(members)

    if not members:
        if running(run, inspect):
            # The last instance's user and indexes go before the cluster stops;
            # if they cannot, the next instance to join deletes them.
            try:
                if wait_ready(run, inspect, timeout=30):
                    result["removed_tokens"] = reconcile_users(run, inspect, {}, removed)
                    write_members(os_dir, {}, [])
            except OpenSearchError:
                pass
            stop_cluster(run, compose_command, os_dir)
            result["changed"] = True
        return result

    if present:
        # Before the cluster is reached, so the instance starts even when it
        # cannot be: its wikis keep the search engine they had.
        wikis = wiki_ids(instance_path)
        result["changed"] |= write_instance_files(
            instance_path, os_dir, token,
            {w: indexed_before(instance_path, w) for w in wikis})

    changed = ensure_certificates(run, inspect, os_dir)
    changed |= _write_if_changed(os.path.join(os_dir, "security", "internal_users.yml"),
                                 render_internal_users(), 0o600)
    changed |= _write_if_changed(os.path.join(os_dir, "security", "roles_mapping.yml"),
                                 render_roles_mapping(), 0o600)
    conf_changed = install_conf(run, inspect, os_dir)
    heap = heap_mb(m["heap"] for m in members.values())
    compose_file = os.path.join(os_dir, "docker-compose.yml")
    _write_if_changed(compose_file, render_compose(heap))
    # The operator's override counts too, so adding or editing it takes
    # effect at the next start, and an image it builds is built again.
    applied = os.path.join(os_dir, ".compose-applied")
    project = hashlib.sha256(
        ((_read(compose_file) or "") + "\0" +
         (_read(os.path.join(os_dir, OVERRIDE_FILE)) or "")).encode()).hexdigest()
    compose_changed = (_read(applied) or "").strip() != project
    result["heap_mb"] = heap
    was_running = running(run, inspect)
    if not was_running or compose_changed or conf_changed:
        start_cluster(run, compose_command, os_dir, recreate=conf_changed and was_running)
        _write_if_changed(applied, project + "\n")
        result["restarted"] = was_running
        changed = True
    if not wait_ready(run, inspect):
        init_security(run, inspect)
        changed = True
        if not wait_ready(run, inspect):
            raise OpenSearchError("OpenSearch security did not come up after setup.")
    result["running"] = True

    result["removed_tokens"] = reconcile_users(run, inspect, members, removed)
    if result["removed_tokens"] or removed:
        write_members(os_dir, {i: m["path"] for i, m in members.items()}, [])

    if present:
        indexed = indexed_wikis(run, inspect, token, wiki_ids(instance_path))
        result["wikis"] = sorted(indexed)
        result["indexed"] = sorted(w for w, built in indexed.items() if built)
        result["changed"] |= write_instance_files(instance_path, os_dir, token, indexed)
    result["changed"] |= changed
    return result


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            instance_id=dict(type="str", required=True),
            instance_path=dict(type="str", required=True),
            member=dict(type="str", choices=["present", "absent"], default="present"),
            compose_command=dict(type="str", default="docker compose"),
            inspect_command=dict(type="str", default="docker"),
            opensearch_dir=dict(type="str"),
        ),
    )
    params = module.params
    os_dir = params["opensearch_dir"] or os.path.join(get_config_dir(), "opensearch")

    def run(argv, env=None, cwd=None, data=None):
        return module.run_command(argv, environ_update=env or {}, cwd=cwd, data=data,
                                  binary_data=data is not None)

    try:
        result = sync(run, params["inspect_command"], params["compose_command"], os_dir,
                      params["instance_id"], params["instance_path"], params["member"])
    except OpenSearchError as exc:
        module.fail_json(msg=str(exc))
        return
    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
