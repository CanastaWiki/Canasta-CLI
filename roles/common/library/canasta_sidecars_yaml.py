#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for managing Canasta sidecars.yaml files.

Provides CRUD + validation on `config/sidecars.yaml`, the instance file
that declares app sidecars (companion services the wiki talks to). This
module only reads/writes/validates the declaration; rendering it into
runtime artifacts (a Compose override layer / Helm values) is separate.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_sidecars_yaml
short_description: Manage Canasta sidecars.yaml
description:
  - Read, import, remove, and validate app sidecars in config/sidecars.yaml.
options:
  instance_path:
    description: Path to the Canasta instance directory.
    type: str
    required: true
  state:
    description: Action to perform.
    type: str
    choices: [read, list, remove, query, validate, import]
    default: read
  name:
    description: Sidecar name (a DNS label); for query/remove.
    type: str
  definitions:
    description: >-
      YAML document of full sidecar definition(s) for import — a single
      sidecar mapping, a list, or a 'sidecars:' list.
    type: str
"""

import os

import yaml

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_validate import (
    validate_sidecar_name,
)


def sidecars_yaml_path(instance_path):
    """Return the path to config/sidecars.yaml, guarded against traversal."""
    path = os.path.join(instance_path, "config", "sidecars.yaml")
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(instance_path)
    if not real_path.startswith(real_base + os.sep):
        raise ValueError(
            "sidecars.yaml path '%s' escapes instance directory '%s'"
            % (real_path, real_base)
        )
    return path


def read_sidecars(instance_path):
    """Read sidecars.yaml and return the list of sidecar dicts."""
    path = sidecars_yaml_path(instance_path)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("sidecars", [])


def write_sidecars(instance_path, sidecars):
    """Write the sidecars list to sidecars.yaml."""
    path = sidecars_yaml_path(instance_path)
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump({"sidecars": sidecars}, f,
                  default_flow_style=False, sort_keys=False)


def sidecar_names(sidecars):
    return [s.get("name") for s in sidecars if isinstance(s, dict)]


def sidecar_exists(sidecars, name):
    return name in sidecar_names(sidecars)


def parse_sidecar_definitions(content):
    """Parse a YAML document of sidecar definition(s) into a list. Accepts a
    `sidecars:` mapping, a bare list, or a single sidecar mapping."""
    data = yaml.safe_load(content)
    if data is None:
        return []
    if isinstance(data, dict) and "sidecars" in data:
        data = data["sidecars"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(
            "sidecar file must be a sidecar mapping, a list of sidecars, "
            "or a 'sidecars:' list")
    return data


def validate_sidecars(sidecars):
    """Return None if every entry is valid, else an error string."""
    seen = set()
    for sidecar in sidecars:
        if not isinstance(sidecar, dict):
            return "each sidecar must be a mapping"
        name = sidecar.get("name")
        err = validate_sidecar_name(name or "")
        if err:
            return err
        if name in seen:
            return "duplicate sidecar name '%s'" % name
        seen.add(name)
        has_image = bool(sidecar.get("image"))
        has_build = bool(sidecar.get("build"))
        if not has_image and not has_build:
            return "sidecar '%s' must set either image or build" % name
        if has_image and has_build:
            return "sidecar '%s' cannot set both image and build" % name
    return None


def run_module():
    module_args = dict(
        instance_path=dict(type="str", required=True),
        state=dict(type="str", default="read",
                   choices=["read", "list", "remove", "query",
                            "validate", "import"]),
        name=dict(type="str", required=False),
        definitions=dict(type="str", required=False),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True,
    )

    instance_path = module.params["instance_path"]
    state = module.params["state"]
    name = module.params.get("name")

    result = {"changed": False}

    if state in ("read", "list"):
        sidecars = read_sidecars(instance_path)
        result["sidecars"] = sidecars
        result["names"] = sidecar_names(sidecars)

    elif state == "query":
        if not name:
            module.fail_json(msg="name is required for query")
            return
        sidecars = read_sidecars(instance_path)
        result["exists"] = sidecar_exists(sidecars, name)
        for sidecar in sidecars:
            if sidecar.get("name") == name:
                result["sidecar"] = sidecar
                break

    elif state == "import":
        content = module.params.get("definitions")
        if not content:
            module.fail_json(msg="definitions is required for import")
            return
        try:
            incoming = parse_sidecar_definitions(content)
        except (yaml.YAMLError, ValueError) as exc:
            module.fail_json(msg="invalid sidecar file: %s" % exc)
            return
        for sidecar in incoming:
            if isinstance(sidecar, dict) and sidecar.get("ports"):
                try:
                    sidecar["ports"] = [int(p) for p in sidecar["ports"]]
                except (TypeError, ValueError):
                    module.fail_json(
                        msg="sidecar '%s' has non-numeric ports"
                        % sidecar.get("name"))
                    return
        err = validate_sidecars(incoming)
        if err:
            module.fail_json(msg=err)
            return
        sidecars = read_sidecars(instance_path)
        existing = set(sidecar_names(sidecars))
        dupes = [s["name"] for s in incoming if s["name"] in existing]
        if dupes:
            module.fail_json(
                msg="sidecar(s) already exist: %s" % ", ".join(dupes))
            return
        sidecars.extend(incoming)
        if incoming and not module.check_mode:
            write_sidecars(instance_path, sidecars)
        result["changed"] = bool(incoming)
        result["names"] = [s["name"] for s in incoming]

    elif state == "remove":
        if not name:
            module.fail_json(msg="name is required for remove")
            return
        sidecars = read_sidecars(instance_path)
        remaining = [s for s in sidecars if s.get("name") != name]
        result["changed"] = len(remaining) != len(sidecars)
        result["removed"] = result["changed"]
        if result["changed"] and not module.check_mode:
            write_sidecars(instance_path, remaining)

    elif state == "validate":
        sidecars = read_sidecars(instance_path)
        err = validate_sidecars(sidecars)
        if err:
            module.fail_json(msg=err)
            return
        result["valid"] = True
        result["count"] = len(sidecars)

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
