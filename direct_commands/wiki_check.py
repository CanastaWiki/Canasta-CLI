#!/usr/bin/env python3
"""wiki-check command — verify MediaWiki main pages are accessible."""

import ssl
import sys
import urllib.parse
import urllib.request
from . import _helpers
from ._helpers import register


def _build_urls(wiki_url):
    base = wiki_url.rstrip("/")
    if base.startswith("http://") or base.startswith("https://"):
        base_urls = [base]
    else:
        parsed = urllib.parse.urlsplit("http://" + base)
        host = parsed.netloc
        path = parsed.path.rstrip("/")
        if path:
            host = host + path

        port = None
        if ":" in parsed.netloc:
            port_value = parsed.netloc.rsplit(":", 1)[-1]
            if port_value.isdigit():
                port = port_value

        base_urls = ["%s://%s" % (protocol, host) for protocol in ["https", "http"]]

    wiki_main_page_suffixes = ["/wiki/Main_Page", "/Main_Page"]
    urls = []
    for base_url in base_urls:
        for suffix in wiki_main_page_suffixes:
            urls.append(base_url + suffix)
    return urls


def _check_url(wiki_url, host):
    for url in _build_urls(wiki_url):
        if _helpers._is_localhost(host):
            req = urllib.request.Request(url)
            if url.startswith("https://"):
                context = ssl._create_unverified_context()
            else:
                context = None
            try:
                with urllib.request.urlopen(req, timeout=15, context=context) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                continue
        else:
            cmd = (
                "curl -sSL -o /dev/null -w '%{http_code}' "
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

        if _check_url(wiki_url, host):
            print("Wiki '%s' is reachable at %s." % (wiki_id, wiki_url))
        else:
            print("Wiki '%s' could not be reached at %s." % (wiki_id, wiki_url))
            all_ok = False

    return 0 if all_ok else 1
