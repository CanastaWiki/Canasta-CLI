#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module to manage an instance's config/composer.local.json.

Adds paths to, or removes paths from, the ``extra.merge-plugin.include``
list of the instance's ``config/composer.local.json`` — the file the
Canasta image consumes at container start to run ``composer update
--no-dev`` with MediaWiki's merge plugin. Existing content (other keys,
other include entries) is preserved; the write is atomic and validated
JSON in, JSON out.

The include paths are relative to MediaWiki's core directory (e.g.
``extensions/FooBar/composer.json``).
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_composer_local
short_description: Manage merge-plugin includes in config/composer.local.json
description:
  - Adds entries to, or removes entries from, the C(extra.merge-plugin.include)
    list of an instance's C(config/composer.local.json), creating the file when
    adding. Existing keys and other entries are preserved; entries are never
    duplicated.
options:
  instance_path:
    description: Path to the instance root (containing config/).
    type: str
    required: true
  include:
    description: Paths to add or remove, relative to MediaWiki's core directory.
    type: list
    elements: str
    required: true
  state:
    description: Whether the given paths should be registered (C(present)) or
      unregistered (C(absent)).
    type: str
    choices: [present, absent]
    default: present
returns:
  changed:
    description: Whether the file was created or modified.
    returned: always
    type: bool
  include:
    description: The full merge-plugin include list after the change.
    returned: always
    type: list
  path:
    description: Absolute path of the managed composer.local.json.
    returned: always
    type: str
  added:
    description: Entries that were added (state=C(present) only).
    returned: When I(state=present)
    type: list
  removed:
    description: Entries that were removed (state=C(absent) only).
    returned: When I(state=absent)
    type: list
"""

import json
import os
import tempfile

from ansible.module_utils.basic import AnsibleModule


def load_composer_local(path):
    """Return (data or None, error message or None) for composer.local.json."""
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return None, ("Cannot parse %s: %s. Fix or remove the file before "
                      "adding composer requirements." % (path, exc))
    if not isinstance(data, dict):
        return None, ("%s is not a JSON object." % path)
    return data, None


def merged_includes(data, include):
    """Return (new include list, entries that were missing)."""
    extra = data.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        data["extra"] = extra
    merge = extra.get("merge-plugin")
    if not isinstance(merge, dict):
        merge = {}
        extra["merge-plugin"] = merge
    current = merge.get("include")
    if current is None:
        current = []
        merge["include"] = current
    if not isinstance(current, list):
        return None, None  # signal caller to fail loudly
    added = [p for p in include if p not in current]
    current.extend(added)
    return current, added


def removed_includes(data, include):
    """Return (new include list, entries that were present and removed)."""
    extra = data.get("extra")
    merge = extra.get("merge-plugin") if isinstance(extra, dict) else None
    current = merge.get("include") if isinstance(merge, dict) else None
    if current is None:
        # Nothing registered at all — removal is a no-op.
        return [], []
    if not isinstance(current, list):
        return None, None  # signal caller to fail loudly
    removed = [p for p in include if p in current]
    remaining = [p for p in current if p not in removed]
    merge["include"] = remaining
    return remaining, removed


def write_json_atomic(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run_module():
    module = AnsibleModule(
        argument_spec={
            "instance_path": {"type": "str", "required": True},
            "include": {"type": "list", "elements": "str",
                        "required": True},
            "state": {"type": "str", "default": "present",
                      "choices": ["present", "absent"]},
        },
        supports_check_mode=True,
    )
    params = module.params
    path = os.path.join(params["instance_path"], "config",
                        "composer.local.json")

    data, error = load_composer_local(path)
    if error:
        module.fail_json(msg=error)

    if params.get("state", "present") == "absent":
        new_list, touched = removed_includes(data, params["include"])
    else:
        new_list, touched = merged_includes(data, params["include"])
    if new_list is None:
        module.fail_json(
            msg=("extra.merge-plugin.include in %s is not a list; refusing "
                 "to overwrite it." % path))

    changed = bool(touched)
    if changed and not module.check_mode:
        write_json_atomic(path, data)

    module.exit_json(changed=changed, include=new_list, path=path,
                     **({"removed": touched}
                        if params.get("state", "present") == "absent"
                        else {"added": touched}))


def main():
    run_module()


if __name__ == "__main__":
    main()
