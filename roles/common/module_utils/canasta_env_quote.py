# -*- coding: utf-8 -*-
"""How a .env value is stored so Docker Compose passes it through literally.

Compose expands `$` in an unquoted or double-quoted .env value, and treats
` #` as the start of a comment, so such a value reaches the containers
changed. Inside single quotes it takes everything literally except `\\'`,
which stands for `'`. A value that would come through unchanged is stored
bare, so .env files and gitops repos keep their existing lines.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
import shlex

_REFERENCE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")

# sed expressions that rewrite each single- or double-quoted KEY=VALUE line
# of a .env to the bare KEY=value a reader gets, for consumers that take the
# file as is (`docker run --env-file`, shell reads). Portable to GNU and BSD
# sed.
DECODE_SED = (
    r"""/^[A-Za-z_][A-Za-z0-9_]*='.*'[[:space:]]*$/{""",
    r"""s/^\([^=]*\)='\(.*\)'[[:space:]]*$/\1=\2/""",
    r"""s/\\'/'/g""",
    "b",
    "}",
    r"""/^[A-Za-z_][A-Za-z0-9_]*=".*"[[:space:]]*$/"""
    r"""s/^\([^=]*\)="\(.*\)"[[:space:]]*$/\1=\2/""",
)


def decode_sed_command(path):
    """A shell command printing the .env at `path` (inserted as is, so
    quote it if needed) with each value decoded as unquote_value does."""
    return "sed %s %s" % (
        " ".join("-e " + shlex.quote(e) for e in DECODE_SED), path)


def needs_quoting(value):
    """True if Compose would read `value` written bare as something else."""
    return (
        "$" in value
        or value != value.strip()
        or value[:1] in ("'", '"', "#")
        or re.search(r"\s#", value) is not None
    )


def quote_value(value):
    """The text to write after `KEY=` for `value`.

    Raises ValueError for a value that needs quoting but has a backslash
    right before a quote or at its end, which Compose's single-quote parser
    cannot represent.
    """
    if not needs_quoting(value):
        return value
    if "\\'" in value or value.endswith("\\"):
        raise ValueError(
            "a value containing $, quotes, ' #' or surrounding spaces cannot "
            "also have a backslash before a quote or at its end")
    return "'%s'" % value.replace("'", "\\'")


def unquote_value(raw):
    """The value a raw .env value text stands for: single quotes as Compose
    reads them, double quotes stripped, anything else as is."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("\\'", "'")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1]
    return raw


def requote_raw(raw, defined):
    """Rewrite a bare value that Compose expands as the literal it was meant
    to be.

    Returns (new_raw, conflict). new_raw is None when the value is left as
    is: it is quoted, has no `$` other than `$$` (Compose's escape for `$`,
    which already comes through as written), or every `$` in it is a
    reference to a key in `defined`, which is interpolation on purpose.
    conflict is True when the value mixes references to
    defined keys with other `$` text, or cannot be quoted, and is left for
    the operator.
    """
    raw = raw.strip()
    if "$" not in raw or raw[:1] in ("'", '"'):
        return None, False
    text = raw.replace("$$", "")
    if "$" not in text:
        return None, False
    names = _REFERENCE.findall(text)
    known = [n for n in names if n in defined]
    if known:
        bare = text.count("$")
        return None, len(known) != bare
    try:
        return quote_value(raw.replace("$$", "$")), False
    except ValueError:
        return None, True
