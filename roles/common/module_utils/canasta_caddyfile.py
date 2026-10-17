# -*- coding: utf-8 -*-
"""Read a Caddyfile's top-level structure.

Shared by the Caddyfile filters (filter_plugins/canasta_caddy.py), which run
on the controller, and the edge module, which runs on the instance's host.
Brace matching respects `#` comments, "..."/`...` strings, and `<<HEREDOCS`,
so stray braces inside them don't miscount.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re


def _skip_string(text, i, quote):
    """text[i] == quote (`"` or backtick). Return index just past the close."""
    n = len(text)
    i += 1
    if quote == "`":  # raw string, no escapes
        while i < n and text[i] != "`":
            i += 1
        return i + 1 if i < n else n
    while i < n:  # double-quoted, backslash escapes
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return n


def _skip_comment(text, i):
    """text[i] == '#'. Return the index of the line's newline (or len)."""
    j = text.find("\n", i)
    return j if j != -1 else len(text)


def _skip_heredoc(text, i):
    """text[i:i+2] == '<<'. Return index at the end of the closing marker line.

    Caddy heredoc: `<<MARKER` opens; a line whose stripped content equals MARKER
    closes it. Content in between (including braces) is literal.
    """
    n = len(text)
    j = i + 2
    m = j
    while m < n and (text[m].isalnum() or text[m] == "_"):
        m += 1
    marker = text[j:m]
    if not marker:  # not a real heredoc; treat '<<' as ordinary chars
        return i + 2
    nl = text.find("\n", m)
    if nl == -1:
        return n
    k = nl + 1
    while k < n:
        line_end = text.find("\n", k)
        if line_end == -1:
            line_end = n
        if text[k:line_end].strip() == marker:
            return line_end
        k = line_end + 1
    return n


def _consume_block(text, i):
    """text[i] == '{'. Return (inner_body, index_just_past_matching_close)."""
    n = len(text)
    depth = 0
    j = i
    while j < n:
        c = text[j]
        if c == "#":
            j = _skip_comment(text, j)
            continue
        if c == '"' or c == "`":
            j = _skip_string(text, j, c)
            continue
        if c == "<" and j + 1 < n and text[j + 1] == "<":
            j = _skip_heredoc(text, j)
            continue
        if c == "{":
            depth += 1
            j += 1
            continue
        if c == "}":
            depth -= 1
            j += 1
            if depth == 0:
                return text[i + 1:j - 1], j
            continue
        j += 1
    return text[i + 1:], n  # unbalanced — return the rest


def _parse_top_level(text):
    """Split into (blocks, inter).

    blocks: list of {label, body, is_global, raw} for each top-level `… { … }`.
    inter:  the text between blocks; len(inter) == len(blocks) + 1, so inter[0]
            is the leading text and inter[k+1] follows blocks[k].
    A block is global when nothing significant (non-comment, non-whitespace)
    precedes its `{`.
    """
    n = len(text)
    blocks = []
    inter = []
    i = 0
    last_end = 0
    sig_start = None  # first significant char of the pending label, if any
    while i < n:
        c = text[i]
        if c == "#":
            i = _skip_comment(text, i)
            continue
        if c == '"' or c == "`":
            if sig_start is None:
                sig_start = i
            i = _skip_string(text, i, c)
            continue
        if c == "<" and i + 1 < n and text[i + 1] == "<":
            i = _skip_heredoc(text, i)
            continue
        if c.isspace():
            i += 1
            continue
        if c == "{":
            label_pos = sig_start if sig_start is not None else i
            is_global = sig_start is None
            body, end = _consume_block(text, i)
            inter.append(text[last_end:label_pos])
            blocks.append({
                "label": text[label_pos:i],
                "body": body,
                "is_global": is_global,
                "raw": text[label_pos:end],
            })
            last_end = end
            sig_start = None
            i = end
            continue
        if sig_start is None:
            sig_start = i
        i += 1
    inter.append(text[last_end:])
    return blocks, inter


def parse_top_level(text):
    """Public name for _parse_top_level."""
    return _parse_top_level(text)


_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _split_address(addr):
    """(scheme, host, port, path) of a Caddy site address."""
    match = re.match(r"^(https?://)?([^/]*)(/.*)?$", addr)
    scheme, hostport, path = match.group(1) or "", match.group(2), match.group(3) or ""
    host, port = hostport, ""
    if ":" in hostport and not hostport.startswith("["):
        host, _, port = hostport.rpartition(":")
    return scheme, host.lower().rstrip("."), port, path


def _site_labels(blocks):
    for block in blocks:
        label = block["label"].strip()
        if not block["is_global"] and not label.startswith("("):
            yield block


def site_hostnames(text):
    """Hostnames the site blocks of a Caddyfile serve, for routing by name.

    Returns (hostnames, skipped): skipped lists (address, reason) for site
    addresses that cannot be routed by hostname alone. Global options blocks
    and snippet definitions are ignored.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    hosts, skipped = [], []
    blocks, _ = _parse_top_level(text.replace("\r\n", "\n"))
    for block in _site_labels(blocks):
        for addr in block["label"].replace(",", " ").split():
            _, host, port, _ = _split_address(addr)
            if not host:
                reason = "it has no hostname"
            elif "*" in host:
                reason = "it is a wildcard"
            elif port not in ("", "80", "443"):
                reason = "it names port %s" % port
            elif host == "localhost" or _IPV4.match(host) or host.startswith("["):
                reason = "it is not a hostname"
            else:
                reason = None
            if reason:
                skipped.append((addr, reason))
            elif host not in hosts:
                hosts.append(host)
    return hosts, skipped


def http_site_addresses(text, hostnames):
    """Text with every site address for one of hostnames given an http://
    scheme, as when a proxy in front terminates TLS for those names."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    wanted = set(hostnames)
    blocks, inter = _parse_top_level(text.replace("\r\n", "\n"))
    sites = {id(b) for b in _site_labels(blocks)}
    parts = [inter[0]]
    for k, block in enumerate(blocks):
        raw = block["raw"]
        if id(block) in sites:
            label = block["label"]
            tokens = re.split(r"(\s*,\s*|\s+)", label)
            for i, token in enumerate(tokens):
                if i % 2:
                    continue
                scheme, host, port, path = _split_address(token)
                if host in wanted and port in ("", "80", "443") and scheme != "http://":
                    tokens[i] = "http://" + host + path
            raw = "".join(tokens) + raw[len(label):]
        parts.append(raw)
        parts.append(inter[k + 1])
    return "".join(parts)
