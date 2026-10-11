#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for managing the Canasta instance registry (conf.json).

Provides CRUD operations on the instance registry stored at conf.json,
including platform-specific config-directory resolution.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_registry
short_description: Manage the Canasta instance registry
description:
  - Read, add, update, and remove Canasta instances from the local registry (conf.json).
  - Also manages registry-level settings (key/value) via set_setting / get_setting states.
  - Operates on the conf.json registry format.
options:
  state:
    description:
      - Desired state of the instance in the registry.
      - >-
        present creates or replaces the whole record from the parameters
        given, so it must be passed every field the record is to keep.
        update merges only the parameters supplied onto an existing
        record and requires the instance to be registered already.
    type: str
    choices:
      - present
      - update
      - absent
      - query
      - query_all
      - query_by_path
      - set_setting
      - get_setting
    default: query
  id:
    description: Canasta instance ID.
    type: str
  path:
    description: Filesystem path of the Canasta instance.
    type: str
  orchestrator:
    description: Container orchestrator (compose or kubernetes).
    type: str
    default: compose
  dev_mode:
    description: Whether development mode is enabled.
    type: bool
    default: false
  build_from:
    description: Local source directory for image builds.
    type: str
  build_args:
    description: Build args (list of KEY=VALUE) to replay on rebuild.
    type: list
    elements: str
  host:
    description: SSH host (user@host or host) for instances on a remote machine.
    type: str
  filter_host:
    description: With state=query_all, return only instances whose host matches this value.
    type: str
  setting_key:
    description: Settings key to read or write (with state=get_setting or state=set_setting).
    type: str
  setting_value:
    description: >-
      Value to write for setting_key (with state=set_setting). Omit or pass null
      to delete the setting.
    type: str
  config_dir:
    description: Override the config directory (instead of auto-detection).
    type: str
  docker_host:
    description: >-
      Docker daemon endpoint (DOCKER_HOST) for the instance's containers,
      stored as dockerHost in the registry.
    type: str
  compose_command:
    description: >-
      Compose command override (e.g. "podman-compose" for Podman).
      Stored as composeCommand in the registry.
    type: str
  inspect_command:
    description: >-
      Inspect command override (e.g. "podman" for Podman).
      Stored as inspectCommand in the registry.
    type: str
"""

import os

from ansible.module_utils.basic import AnsibleModule

# Registry location and lookup helpers are shared with the canasta.py CLI
# wrapper so the two never disagree about where conf.json lives.
from ansible.module_utils.canasta_config import (
    config_path,
    find_by_path,
    get_config_dir,
    is_root,
    read_config,
    write_config,
)

# Re-exported for backwards compatibility with importers/tests that
# referenced canasta_registry.* before the helpers were extracted.
__all__ = [
    "config_path", "find_by_path", "get_config_dir", "is_root",
    "read_config", "write_config", "instance_to_dict", "run_module",
]


# Registry key -> module parameter, for state=update. `id` and `path`
# identify a record rather than describe it, so neither is updatable
# here; a move is a create.
UPDATABLE_FIELDS = (
    ("orchestrator", "orchestrator"),
    ("host", "host"),
    ("devMode", "dev_mode"),
    ("buildFrom", "build_from"),
    ("buildArgs", "build_args"),
    ("dockerHost", "docker_host"),
    ("composeCommand", "compose_command"),
    ("inspectCommand", "inspect_command"),
)


def instance_to_dict(instance):
    """Convert an instance dict to the JSON-serializable registry format."""
    result = {
        "id": instance.get("id", ""),
        "path": instance.get("path", ""),
        "orchestrator": instance.get("orchestrator", "compose"),
    }
    if instance.get("host"):
        result["host"] = instance["host"]
    if instance.get("devMode"):
        result["devMode"] = True
    if instance.get("buildFrom"):
        result["buildFrom"] = instance["buildFrom"]
    if instance.get("buildArgs"):
        result["buildArgs"] = instance["buildArgs"]
    if instance.get("dockerHost"):
        result["dockerHost"] = instance["dockerHost"]
    if instance.get("composeCommand"):
        result["composeCommand"] = instance["composeCommand"]
    if instance.get("inspectCommand"):
        result["inspectCommand"] = instance["inspectCommand"]
    return result


# Fields instance_to_dict writes unconditionally. Everything else it
# emits is optional, and `state: present` rebuilds the record from its
# parameters — so any optional field the caller did not supply would
# vanish, silently erasing either a runtime fact some other code path
# discovered (composeCommand, inspectCommand, dockerHost) or an operator
# setting (devMode).
#
# Derived from instance_to_dict rather than listed by hand: a field added
# there is preserved without anyone having to remember this, which is the
# failure mode that produced the erase in the first place.
_ALWAYS_WRITTEN = ("id", "path", "orchestrator")


def preserved_on_present():
    """Optional registry fields, taken from instance_to_dict itself."""
    probe = instance_to_dict({
        "id": "probe", "path": "/probe", "orchestrator": "compose",
        "host": "probe", "devMode": True, "buildFrom": "/probe",
        "buildArgs": {"probe": "1"}, "dockerHost": "probe",
        "composeCommand": "probe", "inspectCommand": "probe",
    })
    return tuple(k for k in probe if k not in _ALWAYS_WRITTEN)


def _save_instances(module, config_dir, data, instances):
    if not module.check_mode:
        data["Instances"] = instances
        write_config(config_dir, data)


def _state_query(module, config_dir, data, instances):
    inst_id = module.params.get("id")
    inst_path = module.params.get("path")
    if not inst_id and inst_path:
        inst_id, _ = find_by_path(instances, inst_path)
    if not inst_id:
        module.fail_json(msg="Instance ID is required for query (or provide path to auto-detect)")
        return None
    if inst_id not in instances:
        module.fail_json(msg="Instance '%s' not found in registry" % inst_id)
        return None
    instance = dict(instances[inst_id])
    instance["id"] = inst_id
    return {"changed": False, "instance": instance}


def _state_query_all(module, config_dir, data, instances):
    filter_host = module.params.get("filter_host")
    if filter_host:
        # Match against the hostname portion of stored host field,
        # which may be in user@host format.
        def host_matches(stored, target):
            bare = stored.split("@", 1)[-1] if "@" in stored else stored
            return bare == target or stored == target

        selected = {
            k: v for k, v in instances.items()
            if host_matches(v.get("host", "localhost"), filter_host)
        }
    else:
        selected = instances
    return {"changed": False, "instances": selected}


def _state_query_by_path(module, config_dir, data, instances):
    inst_path = module.params.get("path")
    if not inst_path:
        module.fail_json(msg="path is required for query_by_path")
        return None
    found_id, found_inst = find_by_path(instances, inst_path)
    if not found_id:
        module.fail_json(msg="No instance found for path '%s'" % inst_path)
        return None
    instance = dict(found_inst)
    instance["id"] = found_id
    return {"changed": False, "instance": instance}


def _state_present(module, config_dir, data, instances):
    inst_id = module.params.get("id")
    inst_path = module.params.get("path")
    if not inst_id:
        module.fail_json(msg="id is required when state=present")
        return None
    if not inst_path:
        module.fail_json(msg="path is required when state=present")
        return None

    new_instance = instance_to_dict({
        "id": inst_id,
        "path": os.path.abspath(inst_path),
        "orchestrator": module.params["orchestrator"] or "compose",
        "host": module.params.get("host"),
        "devMode": bool(module.params["dev_mode"]),
        "buildFrom": module.params.get("build_from"),
        "buildArgs": module.params.get("build_args"),
        "dockerHost": module.params.get("docker_host"),
        "composeCommand": module.params.get("compose_command"),
        "inspectCommand": module.params.get("inspect_command"),
    })

    changed = False
    if inst_id in instances:
        # Preserve every optional field the caller did not supply, so
        # a caller that sets only compose_command does not erase a
        # prior inspect_command (and vice versa).
        for key in preserved_on_present():
            if key not in new_instance and key in instances[inst_id]:
                new_instance[key] = instances[inst_id][key]
        if instances[inst_id] != new_instance:
            changed = True
            instances[inst_id] = new_instance
    else:
        changed = True
        instances[inst_id] = new_instance

    if changed:
        _save_instances(module, config_dir, data, instances)

    return {"changed": changed, "instance": new_instance}


def _state_update(module, config_dir, data, instances):
    inst_id = module.params.get("id")
    if not inst_id:
        module.fail_json(msg="id is required when state=update")
        return None
    if inst_id not in instances:
        module.fail_json(
            msg="Instance '%s' not found in registry" % inst_id)
        return None

    entry = dict(instances[inst_id])
    for key, param in UPDATABLE_FIELDS:
        value = module.params.get(param)
        if value is None:
            continue
        # Mirrors instance_to_dict: the stored format carries a key
        # only while it holds something, so clearing a field drops
        # the key rather than recording a false or empty value.
        if value:
            entry[key] = value
        else:
            entry.pop(key, None)

    changed = False
    if entry != instances[inst_id]:
        changed = True
        instances[inst_id] = entry
        _save_instances(module, config_dir, data, instances)

    return {"changed": changed, "instance": entry}


def _state_absent(module, config_dir, data, instances):
    inst_id = module.params.get("id")
    if not inst_id:
        module.fail_json(msg="id is required when state=absent")
        return None
    changed = False
    if inst_id in instances:
        changed = True
        if not module.check_mode:
            del instances[inst_id]
            _save_instances(module, config_dir, data, instances)
    return {"changed": changed}


def _state_get_setting(module, config_dir, data, instances):
    setting_key = module.params.get("setting_key")
    if not setting_key:
        module.fail_json(msg="setting_key is required for get_setting")
        return None
    settings = data.get("Settings", {})
    return {"changed": False, "value": settings.get(setting_key)}


def _state_set_setting(module, config_dir, data, instances):
    setting_key = module.params.get("setting_key")
    setting_value = module.params.get("setting_value")
    if not setting_key:
        module.fail_json(msg="setting_key is required for set_setting")
        return None
    settings = data.get("Settings", {})
    old_value = settings.get(setting_key)
    changed = False
    if old_value != setting_value:
        changed = True
        if not module.check_mode:
            if setting_value is None:
                settings.pop(setting_key, None)
            else:
                settings[setting_key] = setting_value
            data["Settings"] = settings
            write_config(config_dir, data)
    return {"changed": changed}


STATE_HANDLERS = {
    "query": _state_query,
    "query_all": _state_query_all,
    "query_by_path": _state_query_by_path,
    "present": _state_present,
    "update": _state_update,
    "absent": _state_absent,
    "get_setting": _state_get_setting,
    "set_setting": _state_set_setting,
}


def run_module():
    module_args = dict(
        state=dict(type="str", default="query",
                   choices=["present", "update", "absent", "query",
                            "query_all", "query_by_path",
                            "set_setting", "get_setting"]),
        setting_key=dict(type="str", required=False),
        setting_value=dict(type="str", required=False),
        id=dict(type="str", required=False),
        path=dict(type="str", required=False),
        # No argument defaults on these two: state=update distinguishes
        # "not supplied" from "supplied false" by testing for None, and a
        # default makes every field look supplied. state=present applies
        # the same defaults itself, below.
        orchestrator=dict(type="str", required=False),
        dev_mode=dict(type="bool", required=False),
        build_from=dict(type="str", required=False),
        build_args=dict(type="list", elements="str", required=False),
        host=dict(type="str", required=False),
        docker_host=dict(type="str", required=False),
        compose_command=dict(type="str", required=False),
        inspect_command=dict(type="str", required=False),
        filter_host=dict(type="str", required=False),
        config_dir=dict(type="str", required=False),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True,
    )

    config_dir = get_config_dir(module.params.get("config_dir"))

    try:
        data = read_config(config_dir)
    except (ValueError, IOError) as e:
        module.fail_json(msg="Failed to read config: %s" % str(e))
        return

    instances = data.get("Instances", {})
    result = STATE_HANDLERS[module.params["state"]](
        module, config_dir, data, instances)
    if result is None:
        return
    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
