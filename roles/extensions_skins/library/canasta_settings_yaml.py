#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for managing Canasta extension/skin settings.yaml files.

Replaces the Go internal/extensionsskins package. Manages the settings.yaml
files that control which extensions and skins are enabled.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_settings_yaml
short_description: Manage Canasta extension/skin settings.yaml
description:
  - Enable, disable, and list extensions and skins in settings.yaml.
  - Supports both global and per-wiki settings.
options:
  instance_path:
    description: Path to the Canasta instance directory.
    type: str
    required: true
  item_type:
    description: Whether managing extensions or skins.
    type: str
    choices: [extensions, skins]
    required: true
  state:
    description: Action to perform.
    type: str
    choices: [read, enable, disable]
    default: read
  names:
    description: List of extension/skin names to enable or disable.
    type: list
    elements: str
  wiki:
    description: Wiki ID for per-wiki settings (omit for global).
    type: str
"""

import os
import re

import yaml

from ansible.module_utils.basic import AnsibleModule


VALID_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")
HEADER = "# Canasta will add and remove lines from this file as extensions and skins are enabled and disabled.\n"


def config_path(instance_path, wiki=None):
    """Return the path to settings.yaml (global or per-wiki), validated against path traversal."""
    if wiki:
        path = os.path.join(instance_path, "config", "settings", "wikis", wiki, "settings.yaml")
    else:
        path = os.path.join(instance_path, "config", "settings", "global", "settings.yaml")
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(instance_path)
    if not real_path.startswith(real_base + os.sep):
        raise ValueError(
            "settings.yaml path '%s' escapes instance directory '%s'"
            % (real_path, real_base)
        )
    return path


def validate_name(name):
    """Validate an extension/skin name."""
    if not name:
        return "name cannot be empty"
    if not VALID_NAME_PATTERN.match(name):
        return ("name '%s' is invalid: must start with alphanumeric, "
                "may contain letters, digits, underscore, dot, hyphen" % name)
    return None


def read_config(path):
    """Read settings.yaml and return the config dict."""
    if not os.path.exists(path):
        return {"extensions": [], "skins": []}
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return {
        "extensions": data.get("extensions") or [],
        "skins": data.get("skins") or [],
    }


def write_config(path, config):
    """Write settings.yaml with the standard header."""
    extensions = config.get("extensions", [])
    skins = config.get("skins", [])
    if not extensions and not skins:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    data = {}
    if extensions:
        data["extensions"] = extensions
    if skins:
        data["skins"] = skins
    with open(path, "w") as f:
        f.write(HEADER)
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def run_module():
    module_args = dict(
        instance_path=dict(type="str", required=True),
        item_type=dict(type="str", required=True, choices=["extensions", "skins"]),
        state=dict(type="str", default="read", choices=["read", "enable", "disable"]),
        names=dict(type="list", elements="str", required=False),
        wiki=dict(type="str", required=False),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True,
    )

    instance_path = module.params["instance_path"]
    item_type = module.params["item_type"]
    state = module.params["state"]
    names = module.params.get("names") or []
    wiki = module.params.get("wiki")

    path = config_path(instance_path, wiki)
    config = read_config(path)
    items = config.get(item_type, [])
    result = {"changed": False}

    if state == "read":
        result["items"] = items

    elif state == "enable":
        if not names:
            module.fail_json(msg="names is required for enable")
            return
        changed = False
        for name in names:
            err = validate_name(name)
            if err:
                module.fail_json(msg=err)
                return
            if name not in items:
                items.append(name)
                changed = True
        if changed:
            items.sort()
            config[item_type] = items
            if not module.check_mode:
                write_config(path, config)
        result["changed"] = changed
        result["items"] = items

    elif state == "disable":
        if not names:
            module.fail_json(msg="names is required for disable")
            return
        changed = False
        for name in names:
            err = validate_name(name)
            if err:
                module.fail_json(msg=err)
                return
            if name in items:
                items.remove(name)
                changed = True
            else:
                module.fail_json(msg="'%s' is not currently enabled" % name)
                return
        if changed:
            config[item_type] = items
            if not module.check_mode:
                write_config(path, config)
        result["changed"] = changed
        result["items"] = items

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
