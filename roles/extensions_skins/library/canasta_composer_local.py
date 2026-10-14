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
    default: []
  state:
    description:
      - Whether the given paths should be registered (C(present)) or
        unregistered (C(absent)).
      - C(prune) unregisters them like C(absent), drops an emptied include
        list, and deletes the file when nothing else in it has content.
      - C(query) changes nothing and reports the string entries of the
        include list, or why the file cannot be read, without failing.
    type: str
    choices: [present, absent, prune, query]
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
    description: Entries that were removed.
    returned: When I(state=absent) or I(state=prune)
    type: list
  removed_names:
    description: The removed entries named by extension or skin, e.g.
      C(OAuth) or C(Vector (skin)); an entry of any other shape keeps its path.
    returned: When I(state=prune)
    type: list
  deleted:
    description: Whether the file was deleted.
    returned: When I(state=prune)
    type: bool
  error:
    description: Why the file cannot be read, or null.
    returned: When I(state=query)
    type: str
"""

import json
import os
import re
import stat
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


def prune_empty(value):
    """Return value without empty lists and objects, recursively.

    Mirrors the image's composer-local.php, which drops them before deciding
    whether the operator's file carries anything.
    """
    if isinstance(value, dict):
        pruned = {k: prune_empty(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if v not in ({}, [])}
    if isinstance(value, list):
        pruned = [prune_empty(v) for v in value]
        return [v for v in pruned if v not in ({}, [])]
    return value


def include_name(path):
    """Name an include by its extension or skin, else return the path."""
    match = re.match(r"^(extensions|skins)/([^/*?\[]+)/composer\.json$", path)
    if not match:
        return path
    return match.group(2) + (" (skin)" if match.group(1) == "skins" else "")


def drop_empty_include(data):
    """Remove an emptied include list, and the objects it leaves empty."""
    extra = data.get("extra")
    merge = extra.get("merge-plugin") if isinstance(extra, dict) else None
    if not isinstance(merge, dict) or merge.get("include") != []:
        return
    del merge["include"]
    if not merge:
        del extra["merge-plugin"]
        if not extra:
            del data["extra"]


# The image writes this file with PHP's JSON_PRETTY_PRINT: four-space
# indentation and unescaped slashes.
DEFAULT_INDENT = 4


def existing_indent(path):
    """Return the indentation of the file at path, so a rewrite only
    changes the lines it has to."""
    try:
        with open(path) as handle:
            for line in handle:
                match = re.match(r"^([ \t]+)\S", line)
                if match:
                    return match.group(1)
    except OSError:
        pass
    return DEFAULT_INDENT


def write_json_atomic(path, data, indent=DEFAULT_INDENT):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    # Preserve the existing file's mode (e.g. the 0644 the image's start-up
    # rsync set) so the replace does not reset it to tempfile's 0600 and make
    # composer.local.json unreadable to the container's web user.
    if os.path.exists(path):
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    else:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data, indent=indent) + "\n")
        os.chmod(tmp, mode)
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
                        "default": []},
            "state": {"type": "str", "default": "present",
                      "choices": ["present", "absent", "prune", "query"]},
        },
        supports_check_mode=True,
    )
    params = module.params
    path = os.path.join(params["instance_path"], "config",
                        "composer.local.json")

    state = params.get("state", "present")
    data, error = load_composer_local(path)
    if state == "query":
        includes = []
        if not error:
            extra = data.get("extra")
            merge = extra.get("merge-plugin") if isinstance(extra, dict) else None
            current = merge.get("include") if isinstance(merge, dict) else None
            if isinstance(current, list):
                includes = [p for p in current if isinstance(p, str)]
        module.exit_json(changed=False, include=includes, path=path,
                         exists=os.path.exists(path), error=error)
    if error:
        module.fail_json(msg=error)

    if state in ("absent", "prune"):
        new_list, touched = removed_includes(data, params["include"])
    else:
        new_list, touched = merged_includes(data, params["include"])
    if new_list is None:
        module.fail_json(
            msg=("extra.merge-plugin.include in %s is not a list; refusing "
                 "to overwrite it." % path))

    changed = bool(touched)
    if state == "prune":
        if changed:
            drop_empty_include(data)
        deleted = changed and not prune_empty(data)
        if deleted and not module.check_mode:
            os.unlink(path)
        elif changed and not module.check_mode:
            write_json_atomic(path, data, existing_indent(path))
        module.exit_json(changed=changed, include=new_list, path=path,
                         removed=touched, deleted=deleted,
                         removed_names=[include_name(p) for p in touched])

    if changed and not module.check_mode:
        write_json_atomic(path, data, existing_indent(path))

    module.exit_json(changed=changed, include=new_list, path=path,
                     **({"removed": touched}
                        if state == "absent"
                        else {"added": touched}))


def main():
    run_module()


if __name__ == "__main__":
    main()
