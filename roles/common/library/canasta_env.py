#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for reading and writing Canasta .env files.

Handles comments, quoted values, and values containing '=' characters.
Values are written so Docker Compose passes them through literally
(canasta_env_quote), and read back the way Compose reads them.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_env
short_description: Manage Canasta .env files
description:
  - Read, set, and unset variables in a Canasta instance .env file.
  - Operates on the standard .env file format.
options:
  path:
    description: Path to the .env file.
    type: str
    required: true
  state:
    description: Action to perform.
    type: str
    choices: [read, read_all, set, unset, lint, requote]
    default: read_all
  key:
    description: Environment variable name.
    type: str
  value:
    description: Value to set (required when state=set).
    type: str
  keys:
    description: List of keys to unset (for state=unset).
    type: list
    elements: str
"""

import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_env_keys import modernize_values, old_names
from ansible.module_utils.canasta_env_quote import (
    quote_value, requote_raw, unquote_value)


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


def requote_lines(lines):
    """Quote each bare value that Compose expands but that was meant
    literally (requote_raw), keeping every other line verbatim.

    Returns (lines, requoted_keys, conflict_keys).
    """
    defined = set(k for k in (_key_of(line) for line in lines) if k)
    out, requoted, conflicts = [], [], []
    for line in lines:
        key = _key_of(line)
        if key is None:
            out.append(line)
            continue
        new_raw, conflict = requote_raw(raw_value_of(line), defined)
        if conflict:
            conflicts.append(key)
        if new_raw is None:
            out.append(line)
        else:
            out.append("%s=%s" % (key, new_raw))
            requoted.append(key)
    return out, requoted, conflicts


def entries_to_dict(entries):
    """Convert parsed entries to a dict (last value wins for duplicates)."""
    result = {}
    for key, value, is_comment in entries:
        if not is_comment and key:
            result[key] = value
    return result


def run_module():
    module_args = dict(
        path=dict(type="str", required=True),
        state=dict(type="str", default="read_all",
                   choices=["read", "read_all", "set", "unset", "lint", "requote"]),
        key=dict(type="str", required=False),
        value=dict(type="str", required=False, no_log=True),
        keys=dict(type="list", elements="str", required=False),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True,
    )

    path = module.params["path"]
    state = module.params["state"]
    key = module.params.get("key")
    value = module.params.get("value")
    keys = module.params.get("keys") or []

    # Read existing content
    if os.path.exists(path):
        with open(path, "r") as f:
            content = f.read()
    else:
        content = ""

    entries = parse_env_file(content)
    file_dict = entries_to_dict(entries)
    # Readers see renamed keys under their new names, even in a .env
    # written before the rename.
    env_dict = modernize_values(file_dict)
    changed = False
    result = {"changed": False}

    if state == "read_all":
        result["variables"] = env_dict

    elif state == "lint":
        result["has_crlf"] = lint_env_file(content)

    elif state == "requote":
        lines, requoted, conflicts = requote_lines(parse_env_lines(content))
        result["requoted_keys"] = requoted
        result["conflict_keys"] = conflicts
        if requoted:
            changed = True
            if not module.check_mode:
                with open(path, "w") as f:
                    f.write(lines_to_content(lines))
        result["changed"] = changed

    elif state == "read":
        if not key:
            module.fail_json(msg="key is required when state=read")
            return
        if key in env_dict:
            result["value"] = env_dict[key]
            result["found"] = True
        else:
            result["value"] = ""
            result["found"] = False

    elif state == "set":
        if not key:
            module.fail_json(msg="key is required when state=set")
            return
        if value is None:
            module.fail_json(msg="value is required when state=set")
            return
        lines = parse_env_lines(content)
        try:
            new_lines = set_line(lines, key, value)
        except ValueError as e:
            module.fail_json(msg=str(e))
            return
        # Compare against the raw value as it would be written, so a
        # quote-only change (KEY="secret" -> KEY=secret) is detected and
        # repaired, and an identical line stays idempotent.
        old_raw = None
        for line in lines:
            if _key_of(line) == key:
                old_raw = raw_value_of(line)
                break
        stale = [k for k in old_names(key) if k in file_dict]
        if old_raw != quote_value(value) or stale:
            changed = True
            for k in stale:
                new_lines = unset_lines(new_lines, k)
            if not module.check_mode:
                new_content = lines_to_content(new_lines)
                with open(path, "w") as f:
                    f.write(new_content)
        result["changed"] = changed

    elif state == "unset":
        if key:
            keys = [key]
        if not keys:
            module.fail_json(msg="key or keys is required when state=unset")
            return
        lines = parse_env_lines(content)
        for k in keys:
            for name in [k] + old_names(k):
                if name in file_dict:
                    changed = True
                    lines = unset_lines(lines, name)
                    del file_dict[name]
        if changed and not module.check_mode:
            new_content = lines_to_content(lines)
            with open(path, "w") as f:
                f.write(new_content)
        result["changed"] = changed

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
