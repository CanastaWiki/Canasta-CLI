# -*- coding: utf-8 -*-
"""The canasta_edge module's handling of acme.sh: running it in the
acme.sh container, reading its per-domain conf files and the certificates
it issued, and turning its output into a failure the operator can act on."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os
import re
import shlex
import time

try:
    from ansible.module_utils.canasta_atomic_write import read_text as _read
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_atomic_write import read_text as _read
try:
    from ansible.module_utils.canasta_edge_render import CERT_DIR, EdgeError
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_edge_render import CERT_DIR, EdgeError


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


def _leaf(pem):
    """The first certificate in PEM text, without whitespace, or None."""
    begin, end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    start = (pem or "").find(begin)
    stop = (pem or "").find(end, start)
    if start < 0 or stop < 0:
        return None
    return "".join(pem[start:stop + len(end)].split())


def pem_holds_issued(edge_dir, host):
    """Whether HAProxy's PEM for host holds the certificate acme.sh keeps for
    it; None when acme.sh's copy cannot be read."""
    leaf = _leaf(_read(os.path.join(edge_dir, "acme", "%s_ecc" % host, "%s.cer" % host)))
    if leaf is None:
        return None
    pem = _read(os.path.join(edge_dir, "conf", "certs", "%s.pem" % host)) or ""
    return leaf in "".join(pem.split())


def served(edge_dir, host):
    """Whether HAProxy's PEM for host holds the certificate acme.sh keeps for
    it. True when acme.sh's copy cannot be read, as there is nothing to
    install then."""
    return pem_holds_issued(edge_dir, host) is not False


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


# acme.sh ends a failed deploy with these, after the hook's own error.
ACME_DEPLOY_CLOSING_LINES = ("Error deploying for domain", "Error encountered while deploying")


def deploy_failure(out, err):
    """The hook's own error from a failed acme.sh deploy."""
    lines = [ACME_TIMESTAMP.sub("", l).strip() for l in (out + "\n" + err).splitlines()]
    lines = [l for l in lines if l and not l.startswith(ACME_DEPLOY_CLOSING_LINES)]
    return lines[-1].rstrip(".") if lines else ""


def acme_hint(kind, host):
    hint = ACME_PROBLEM_HINTS.get(kind, ACME_DEFAULT_HINT)
    return hint % host if "%s" in hint else hint


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
