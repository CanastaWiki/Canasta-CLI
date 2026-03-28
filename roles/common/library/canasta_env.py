#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for reading and writing Canasta .env files.

Replaces the Go canasta.GetEnvVariable/SaveEnvVariable functions.
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
  - Compatible with the Go CLI's .env file format.
options:
  path:
    description: Path to the .env file.
    type: str
    required: true
  state:
    description: Action to perform.
    type: str
    choices: [read, read_all, set, unset]
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

    Preserves ordering, comments, and blank lines for faithful round-tripping.
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
            # Strip surrounding double quotes (matching Go behavior)
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            entries.append((key, value, False))
        else:
            # Malformed line, preserve as-is
            entries.append((None, line, True))
    return entries


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

    Matches Go behavior: updates first occurrence, skips duplicate lines for same key.
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
        state=dict(type="str", default="read_all", choices=["read", "read_all", "set", "unset"]),
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
        old_value = env_dict.get(key)
        if old_value != value:
            changed = True
            entries = set_variable(entries, key, value)
            if not module.check_mode:
                new_content = entries_to_content(entries)
                with open(path, "w") as f:
                    f.write(new_content)
        result["changed"] = changed

    elif state == "unset":
        if key:
            keys = [key]
        if not keys:
            module.fail_json(msg="key or keys is required when state=unset")
            return
        for k in keys:
            if k in env_dict:
                changed = True
                entries = unset_variable(entries, k)
                del env_dict[k]
        if changed and not module.check_mode:
            new_content = entries_to_content(entries)
            with open(path, "w") as f:
                f.write(new_content)
        result["changed"] = changed

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
