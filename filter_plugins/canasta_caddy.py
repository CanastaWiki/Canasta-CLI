# Jinja filter to meld a Caddyfile's top-level global options blocks into one.
#
# Caddy permits exactly one global options block and it must come first. Canasta
# inlines a user-managed Caddyfile.global (which may itself contain a global
# options block) alongside its own CLI-generated global directives (crowdsec,
# trusted_proxies, staging acme_ca). Without melding, that yields two global
# blocks and Caddy refuses to load.
#
# This filter scans the assembled Caddyfile at the top level — brace matching
# that respects `#` comments, "..."/`...` strings, and `<<HEREDOCS` so stray
# braces inside them don't miscount — merges the bodies of every global block
# into a single block placed first, and leaves site blocks / snippets in their
# original order.
#
# Loaded via the `filter_plugins` path in ansible.cfg (see canasta_crowdsec.py).

import os
import re
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "roles", "common", "module_utils"))

from canasta_caddyfile import (  # noqa: E402
    _parse_top_level, http_site_addresses, site_hostnames,
)

try:
    from ansible.utils.unsafe_proxy import wrap_var
except ImportError:  # pragma: no cover - unexpected on supported ansible-core
    def wrap_var(value):
        return value


def caddy_unsafe(value):
    """Mark a string unsafe so a literal {{ … }} in the user's Caddyfile.global
    is never re-evaluated as a template when inlined into the Caddyfile."""
    return wrap_var(value)


def _meld_servers_blocks(body):
    """Merge `servers` blocks of a global options body that name the same
    listener address; Caddy rejects two of them."""
    blocks, inter = _parse_top_level(body)
    groups = {}
    for b in blocks:
        tokens = b["label"].rstrip().rsplit("\n", 1)[-1].split()
        if tokens and tokens[0] == "servers":
            groups.setdefault(tuple(tokens[1:]), []).append(b)
    if all(len(g) < 2 for g in groups.values()):
        return body

    parts = [inter[0]]
    for k, b in enumerate(blocks):
        label = b["label"]
        cut = label.rstrip().rfind("\n") + 1
        prefix, line = label[:cut], label[cut:]
        tokens = line.split()
        group = groups.get(tuple(tokens[1:])) if tokens and tokens[0] == "servers" else None
        if group is None or len(group) < 2:
            parts.append(b["raw"])
        elif group[0] is b:
            bodies = [g["body"].rstrip(" \t") for g in group[:-1]] + [group[-1]["body"]]
            parts.append(prefix + line + "{" + "".join(bodies) + "}")
        else:
            parts.append(prefix)
        parts.append(inter[k + 1])
    return "".join(parts)


def meld_caddy_global_blocks(text):
    """Meld all top-level global options blocks into a single leading block."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = text.replace("\r\n", "\n")

    blocks, inter = _parse_top_level(text)
    global_bodies = [b["body"] for b in blocks if b["is_global"]]
    has_global = any(b.strip() for b in global_bodies)

    parts = [inter[0]]
    if has_global:
        merged = _meld_servers_blocks("".join(global_bodies)).strip("\n")
        parts.append("\n{\n" + merged + "\n}\n")
    for k, b in enumerate(blocks):
        if not b["is_global"]:
            parts.append(b["raw"])
        parts.append(inter[k + 1])

    result = "".join(parts)
    # collapse runs of blank lines left by excised global blocks
    result = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", result)
    return result.strip("\n") + "\n"


def caddy_explicit_http_hosts(text):
    """Hostnames the given Caddyfile text serves with an `http://` address.

    A bare `host` label is not one of these: it yields the https server plus
    Caddy's automatic redirect, which the generated redirect server displaces
    rather than collides with.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    hosts = []
    blocks, _ = _parse_top_level(text.replace("\r\n", "\n"))
    for block in blocks:
        if block["is_global"]:
            continue
        for addr in block["label"].replace(",", " ").split():
            if addr.startswith("http://"):
                host = addr[len("http://"):].split("/")[0].strip()
                if host and host not in hosts:
                    hosts.append(host)
    return hosts


class FilterModule(object):
    def filters(self):
        return {
            "meld_caddy_global_blocks": meld_caddy_global_blocks,
            "caddy_unsafe": caddy_unsafe,
            "caddy_explicit_http_hosts": caddy_explicit_http_hosts,
            "caddy_site_hostnames": lambda text: site_hostnames(text)[0],
            "caddy_http_site_addresses": http_site_addresses,
        }
