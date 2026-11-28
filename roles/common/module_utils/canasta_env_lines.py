# -*- coding: utf-8 -*-
"""Reading and rewriting .env files line by line, keeping untouched lines
verbatim. Shared by the canasta_env module and direct_commands."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

try:
    from ansible.module_utils.canasta_env_quote import quote_value, unquote_value
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_env_quote import quote_value, unquote_value


def parse_env_file(content):
    """Parse a .env file into an ordered list of (key, value, is_comment) tuples.

    `value` is unquoted the way Compose reads it (unquote_value). Preserves ordering,
    comments, and blank lines. To rewrite the file while keeping untouched
    lines (and their quoting) verbatim, use parse_env_lines / set_line /
    unset_lines / lines_to_content instead.
    """
    entries = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            entries.append((None, line, True))
            continue
        parts = stripped.split("=", 1)
        if len(parts) == 2:
            key = parts[0].strip()
            entries.append((key, unquote_value(parts[1]), False))
        else:
            # Malformed line, preserve as-is
            entries.append((None, line, True))
    return entries


def _key_of(line):
    """Return the key defined by a raw .env line, or None for comments,
    blanks, and malformed lines. Tolerates a trailing CR (CRLF files)."""
    stripped = line.rstrip("\r").strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split("=", 1)
    if len(parts) != 2:
        return None
    return parts[0].strip()


def raw_value_of(line):
    """Return the un-stripped value text of a raw 'key=value' line (quotes
    intact), or None if the line does not define a key."""
    if _key_of(line) is None:
        return None
    return line.rstrip("\r").strip().split("=", 1)[1].strip()


def parse_env_lines(content):
    """Split content into raw lines, preserving them verbatim for rewriting."""
    return content.split("\n")


def lines_to_content(lines):
    """Join lines into .env content that ends with a newline, so a line
    appended with `echo KEY=value >> .env` starts a line of its own."""
    content = "\n".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    return content


def set_line(lines, key, value):
    """Rewrite the first line defining `key` as 'key=value', the value
    quoted only if Compose would otherwise change it (quote_value), dropping
    later duplicates and appending if absent. Untouched lines are kept
    verbatim so their quoting (and any inline '#') survives.

    Raises ValueError for a value containing CR or LF, which would write
    lines of its own, or one quote_value cannot store.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(
            "The value of %s contains a line break; .env values must be "
            "a single line." % key)
    try:
        value = quote_value(value)
    except ValueError as e:
        raise ValueError("The value of %s cannot be stored in .env: %s."
                         % (key, e))
    found = False
    new_lines = []
    for line in lines:
        if _key_of(line) == key:
            if not found:
                new_lines.append("%s=%s" % (key, value))
                found = True
            # Drop duplicate definitions of the same key.
        else:
            new_lines.append(line)
    if not found:
        # A trailing "" is the file's final newline; the new line goes
        # before it.
        at = len(new_lines)
        if at and new_lines[-1] == "":
            at -= 1
        new_lines.insert(at, "%s=%s" % (key, value))
    return new_lines


def unset_lines(lines, key):
    """Remove every line defining `key`, keeping other lines verbatim."""
    return [line for line in lines if _key_of(line) != key]


def lint_env_file(content):
    """True if .env uses CRLF line endings. Readers tolerate them, but a
    consumer that takes the file as is (`docker run --env-file`) sees the
    CR as part of each value; for restic that surfaces as an opaque "wrong
    password" / auth failure."""
    return "\r" in content


def entries_to_dict(entries):
    """Convert parsed entries to a dict (last value wins for duplicates)."""
    result = {}
    for key, value, is_comment in entries:
        if not is_comment and key:
            result[key] = value
    return result
