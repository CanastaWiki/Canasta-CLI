# -*- coding: utf-8 -*-
"""The .env keys renamed when the HAProxy edge took over TLS.

A .env written before the rename, and a gitops env.template that still uses
the old names, keep working: readers see the new names, and writers store
under whichever name a template already has.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

RENAMED_ENV_KEYS = {
    "CADDY_TLS_MODE": "TLS_MODE",
    "CADDY_TRUSTED_PROXIES": "TRUSTED_PROXIES",
    "CADDY_TLS_CERT": "TLS_CERT",
    "CADDY_TLS_KEY": "TLS_KEY",
}
# CADDY_AUTO_HTTPS=off became TLS_MODE=off; "on" was the default.
PLAIN_HTTP_KEY = "CADDY_AUTO_HTTPS"


def old_names(key):
    """The old keys whose value a new key takes over."""
    names = [old for old, new in RENAMED_ENV_KEYS.items() if new == key]
    if key == "TLS_MODE":
        names.append(PLAIN_HTTP_KEY)
    return names


def _plain(values):
    return str(values.get(PLAIN_HTTP_KEY, "")).strip().lower() == "off"


def modernize_values(values):
    """A copy of a .env mapping with each renamed key also under its new
    name. A key already set under its new name wins, and
    CADDY_AUTO_HTTPS=off wins over CADDY_TLS_MODE."""
    out = dict(values)
    for old, new in RENAMED_ENV_KEYS.items():
        if new not in values and old in values:
            out[new] = values[old]
    if "TLS_MODE" not in values and _plain(values):
        out["TLS_MODE"] = "off"
    return out


def _line_key(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def modernize_text(text):
    """Rewrite the renamed keys of a .env-style text to their new names,
    with the same precedence as modernize_values."""
    lines = str(text or "").split("\n")
    present = {}
    for line in lines:
        key = _line_key(line)
        if key:
            present[key] = line.split("=", 1)[1]
    plain = _plain(present)
    out = []
    for line in lines:
        key = _line_key(line)
        if key == PLAIN_HTTP_KEY:
            if plain and "TLS_MODE" not in present:
                out.append("TLS_MODE=off")
                present["TLS_MODE"] = "off"
            continue
        if key in RENAMED_ENV_KEYS:
            new = RENAMED_ENV_KEYS[key]
            if new not in present and not (new == "TLS_MODE" and plain):
                value = line.split("=", 1)[1]
                out.append("%s=%s" % (new, value))
                present[new] = value
            continue
        out.append(line)
    return "\n".join(out)
