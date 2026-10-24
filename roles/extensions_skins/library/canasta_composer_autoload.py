#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module to find composer.json autoload.files entries that are
missing from an extension's or skin's checkout.

Composer writes every ``autoload.files`` entry into
``vendor/composer/autoload_files.php`` whether or not the file exists, and
the autoloader then fails on every request, so a missing entry must be
caught before the composer.json is registered.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_composer_autoload
short_description: Report composer.json autoload.files entries that do not exist
description:
  - Reads each given composer.json and reports the C(autoload.files) entries
    that do not exist relative to its directory. Changes nothing. A
    composer.json that is missing or cannot be parsed is not reported; composer
    reports that itself.
options:
  instance_path:
    description: Path to the instance root.
    type: str
    required: true
  include:
    description: composer.json paths relative to the instance root (e.g.
      C(extensions/Foo/composer.json)), as registered in
      config/composer.local.json.
    type: list
    elements: str
    default: []
returns:
  broken:
    description: One entry per composer.json with missing files, each with
      C(include) (the given path) and C(missing) (the missing entries as
      written in the composer.json).
    returned: always
    type: list
"""

import json
import os

from ansible.module_utils.basic import AnsibleModule


def missing_autoload_files(composer_json):
    """Return the autoload.files entries of composer_json that do not exist."""
    try:
        with open(composer_json) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    autoload = data.get("autoload") if isinstance(data, dict) else None
    files = autoload.get("files") if isinstance(autoload, dict) else None
    if not isinstance(files, list):
        return []
    base = os.path.dirname(composer_json)
    return [f for f in files
            if isinstance(f, str) and not os.path.isfile(os.path.join(base, f))]


def run_module():
    module = AnsibleModule(
        argument_spec={
            "instance_path": {"type": "str", "required": True},
            "include": {"type": "list", "elements": "str", "default": []},
        },
        supports_check_mode=True,
    )
    broken = []
    for include in module.params["include"]:
        missing = missing_autoload_files(
            os.path.join(module.params["instance_path"], include))
        if missing:
            broken.append({"include": include, "missing": missing})
    module.exit_json(changed=False, broken=broken)


def main():
    run_module()


if __name__ == "__main__":
    main()
