#!/usr/bin/env python3
"""wiki-check command — verify MediaWiki instances are accessible."""

import ssl
import sys
import urllib.parse
import urllib.request
from . import _helpers
from ._helpers import register


_PROTOCOLS = ("https", "http")
_LOCAL_DOMAINS = {"localhost", "127.0.0.1"}


def _build_urls(wiki_url):
    base = wiki_url.rstrip("/")
    if base.startswith("http://") or base.startswith("https://"):
        return [base + "/"]

    parsed = urllib.parse.urlsplit("http://" + base)
    host = parsed.netloc
    path = parsed.path.rstrip("/")
    if path:
        host = host + path

    return [f"{protocol}://{host}/" for protocol in _PROTOCOLS]


def _localhost_probe(url, instance_path):
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme
    domain = parsed.netloc
    url_path = parsed.path or "/"

    bare_hostname = domain.split(":")[0]

    if bare_hostname in _LOCAL_DOMAINS:
        req = urllib.request.Request(url)
        context = ssl._create_unverified_context() if scheme == "https" else None
        try:
            with urllib.request.urlopen(req, timeout=15, context=context) as resp:
                return resp.status == 200
        except Exception:
            return False

    env = _helpers._read_env_file(instance_path, "localhost") if instance_path else {}
    if scheme == "https":
        port = env.get("HTTPS_PORT", "")
    else:
        port = env.get("HTTP_PORT", "")

    if port:
        check_url = f"{scheme}://127.0.0.1:{port}{url_path}"
    else:
        check_url = url

    req = urllib.request.Request(check_url)
    req.add_header("Host", domain)

    if scheme == "https":
        context = ssl._create_unverified_context()
    else:
        context = None

    try:
        with urllib.request.urlopen(req, timeout=15, context=context) as resp:
            return resp.status == 200
    except Exception:
        return False


def _check_url(wiki_url, host, instance_path=""):
    for url in _build_urls(wiki_url):
        if _helpers._is_localhost(host):
            if _localhost_probe(url, instance_path):
                return True
        else:
            cmd = (
                "curl -sSLk -o /dev/null -w '%{http_code}' "
                + _helpers._shell_quote(url)
            )
            rc, stdout = _helpers._ssh_run(host, cmd)
            if not stdout.strip():
                continue
            try:
                if int(stdout.strip()) == 200:
                    return True
            except ValueError:
                continue
    return False


@register("wiki_check")
def cmd_wiki_check(args):
    instance_id, instance = _helpers._resolve_instance(args)
    host = getattr(args, "host", None) or instance.get("host") or "localhost"
    path = instance.get("path", "")
    wikis = _helpers._read_wikis(path, host)

    if not wikis:
        print(
            "Error: no wikis configured for instance '%s'" % instance_id,
            file=sys.stderr,
        )
        return 1

    print("Checking Canasta Wiki: %s" % instance_id)

    all_ok = True
    for wiki in wikis:
        wiki_id = wiki.get("id")
        wiki_url = wiki.get("url", "").strip()
        if not wiki_url:
            print("Wiki '%s' failed: missing wiki URL in wikis.yaml." % wiki_id)
            all_ok = False
            continue

        if _check_url(wiki_url, host, instance_path=path):
            print("Wiki '%s' is reachable at %s." % (wiki_id, wiki_url))
        else:
            print("Wiki '%s' could not be reached at %s." % (wiki_id, wiki_url))
            all_ok = False

    return 0 if all_ok else 1
