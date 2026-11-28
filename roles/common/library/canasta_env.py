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
from ansible.module_utils.canasta_atomic_write import replace_file
from ansible.module_utils.canasta_env_keys import modernize_values, old_names
from ansible.module_utils.canasta_env_lines import (
    _key_of, entries_to_dict, lines_to_content, lint_env_file, parse_env_file, parse_env_lines,
    raw_value_of, set_line, unset_lines)
from ansible.module_utils.canasta_env_quote import quote_value, requote_raw


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


def _write(module, path, content):
    try:
        replace_file(path, content)
    except OSError as e:
        module.fail_json(msg="Could not write %s: %s" % (path, e))


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
                _write(module, path, lines_to_content(lines))
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
                _write(module, path, lines_to_content(new_lines))
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
            _write(module, path, lines_to_content(lines))
        result["changed"] = changed

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
