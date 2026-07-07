#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for reading and writing Canasta .env files.

Handles comments, quoted values, and values containing '=' characters.
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
    choices: [read, read_all, set, unset, lint]
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


def parse_env_file(content):
    """Parse a .env file into an ordered list of (key, value, is_comment) tuples.

    `value` has surrounding quotes stripped, for reads. Preserves ordering,
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
            value = parts[1].strip()
            # Strip surrounding quotes (double or single) for the read value.
            if len(value) >= 2:
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
            entries.append((key, value, False))
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
    return "\n".join(lines)


def set_line(lines, key, value):
    """Rewrite the first line defining `key` as an unquoted 'key=value',
    dropping later duplicates and appending if absent. Untouched lines are
    kept verbatim so their quoting (and any inline '#') survives.
    """
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
        new_lines.append("%s=%s" % (key, value))
    return new_lines


def unset_lines(lines, key):
    """Remove every line defining `key`, keeping other lines verbatim."""
    return [line for line in lines if _key_of(line) != key]


def lint_env_file(content):
    """Find .env hygiene problems that survive read-time quote-stripping but
    reach `docker --env-file` literally.

    parse_env_file() strips surrounding quotes when reading, so a value like
    RESTIC_PASSWORD="secret" looks correct via `canasta config get` — but
    Docker's --env-file does NOT strip quotes, so the container sees the
    quotes (and any trailing CR from CRLF endings) as part of the value. For
    restic that surfaces as an opaque "wrong password" / auth failure.

    Returns (quoted_keys, has_crlf): the keys whose values are wrapped in
    matching quotes, and whether the file uses CRLF line endings.
    """
    has_crlf = "\r" in content
    quoted_keys = []
    for line in content.split("\n"):
        # Tolerate a trailing CR so key detection still works on CRLF files.
        stripped = line.rstrip("\r").strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("=", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            quoted_keys.append(key)
    return quoted_keys, has_crlf


def entries_to_dict(entries):
    """Convert parsed entries to a dict (last value wins for duplicates)."""
    result = {}
    for key, value, is_comment in entries:
        if not is_comment and key:
            result[key] = value
    return result


def entries_to_content(entries):
    """Serialize entries back to .env file content."""
    lines = []
    for key, value, is_comment in entries:
        if is_comment:
            lines.append(value)
        else:
            lines.append("%s=%s" % (key, value))
    return "\n".join(lines)


def set_variable(entries, key, value):
    """Set a variable, updating in place if it exists or appending if not.

    Updates the first occurrence, skips duplicate lines for the same key.
    """
    found = False
    new_entries = []
    for entry_key, entry_value, is_comment in entries:
        if not is_comment and entry_key == key:
            if not found:
                new_entries.append((key, value, False))
                found = True
            # Skip duplicate lines for same key
        else:
            new_entries.append((entry_key, entry_value, is_comment))
    if not found:
        new_entries.append((key, value, False))
    return new_entries


def unset_variable(entries, key):
    """Remove all lines for a given key."""
    return [(k, v, c) for k, v, c in entries if c or k != key]


def run_module():
    module_args = dict(
        path=dict(type="str", required=True),
        state=dict(type="str", default="read_all", choices=["read", "read_all", "set", "unset", "lint"]),
        key=dict(type="str", required=False),
        value=dict(type="str", required=False),
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
    env_dict = entries_to_dict(entries)
    changed = False
    result = {"changed": False}

    if state == "read_all":
        result["variables"] = env_dict

    elif state == "lint":
        quoted_keys, has_crlf = lint_env_file(content)
        result["quoted_keys"] = quoted_keys
        result["has_crlf"] = has_crlf

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
        # Compare against the raw (un-stripped) value so a quote-only change
        # (KEY="secret" -> KEY=secret) is detected and repaired, and so an
        # already-clean identical value stays idempotent.
        old_raw = None
        for line in lines:
            if _key_of(line) == key:
                old_raw = raw_value_of(line)
                break
        if old_raw != value:
            changed = True
            new_lines = set_line(lines, key, value)
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
            if k in env_dict:
                changed = True
                lines = unset_lines(lines, k)
                del env_dict[k]
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
