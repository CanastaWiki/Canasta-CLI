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

import re

try:
    from ansible.utils.unsafe_proxy import wrap_var
except ImportError:  # pragma: no cover - unexpected on supported ansible-core
    def wrap_var(value):
        return value


def caddy_unsafe(value):
    """Mark a string unsafe so a literal {{ … }} in the user's Caddyfile.global
    is never re-evaluated as a template when inlined into the Caddyfile."""
    return wrap_var(value)


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
        merged = "".join(global_bodies).strip("\n")
        parts.append("\n{\n" + merged + "\n}\n")
    for k, b in enumerate(blocks):
        if not b["is_global"]:
            parts.append(b["raw"])
        parts.append(inter[k + 1])

    result = "".join(parts)
    # collapse runs of blank lines left by excised global blocks
    result = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", result)
    return result.strip("\n") + "\n"


class FilterModule(object):
    def filters(self):
        return {
            "meld_caddy_global_blocks": meld_caddy_global_blocks,
            "caddy_unsafe": caddy_unsafe,
        }
