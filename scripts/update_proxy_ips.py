#!/usr/bin/env python3
"""Refresh the vendored CDN/WAF edge IP ranges used by
CADDY_TRUSTED_PROXIES.

Fetches the current published ranges for each supported provider and
rewrites the YAML list in roles/orchestrator/vars/<provider>_ips.yml,
preserving the human-written header comment and updating its
'Last synced:' date. The proxy-ips GitHub workflow runs this on a
schedule and opens a PR when a list changes.

Usage:
    python scripts/update_proxy_ips.py            # update all providers
    python scripts/update_proxy_ips.py cloudflare # one provider
"""

import datetime
import json
import os
import re
import sys
import urllib.request


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VARS_DIR = os.path.join(REPO_ROOT, "roles", "orchestrator", "vars")


def _get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def fetch_cloudflare():
    """Cloudflare publishes plain-text CIDR lists, one per line."""
    v4 = _get("https://www.cloudflare.com/ips-v4")
    v6 = _get("https://www.cloudflare.com/ips-v6")
    ranges = [line.strip() for line in (v4 + "\n" + v6).splitlines()]
    return [r for r in ranges if r]


def fetch_imperva():
    """Imperva's integration API returns JSON {ipRanges, ipv6Ranges};
    open, no auth required."""
    body = _get(
        "https://my.imperva.com/api/integration/v1/ips",
        data=b"",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    payload = json.loads(body)
    return list(payload.get("ipRanges", [])) + list(payload.get("ipv6Ranges", []))


PROVIDERS = {
    "cloudflare": ("cloudflare_ip_ranges", fetch_cloudflare),
    "imperva": ("imperva_ip_ranges", fetch_imperva),
}


def update_provider(name):
    var_name, fetch = PROVIDERS[name]
    path = os.path.join(VARS_DIR, "%s_ips.yml" % name)
    cidrs = fetch()
    if not cidrs:
        raise SystemExit("Refusing to write an empty %s range list" % name)

    with open(path) as f:
        text = f.read()

    # Preserve everything up to and including the "<var>:" key line;
    # replace the list below it. Keeps the curated header comment intact.
    marker = "\n%s:" % var_name
    head = text[: text.index(marker) + 1]
    head = re.sub(
        r"Last synced: \d{4}-\d{2}-\d{2}",
        "Last synced: %s" % datetime.date.today().isoformat(),
        head,
    )
    body = "%s:\n" % var_name + "".join('  - "%s"\n' % c for c in cidrs)

    with open(path, "w") as f:
        f.write(head + body)
    print("Updated %s (%d ranges)" % (path, len(cidrs)))


def main():
    names = sys.argv[1:] or list(PROVIDERS)
    for name in names:
        if name not in PROVIDERS:
            raise SystemExit("Unknown provider: %s" % name)
        update_provider(name)


if __name__ == "__main__":
    main()
