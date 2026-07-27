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
    description: Desired state of the instance in the registry.
    type: str
    choices:
      - present
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
  managed_cluster:
    description: Whether the CLI manages the K8s cluster.
    type: bool
    default: false
  registry:
    description: Container registry for K8s image push.
    type: str
  kind_cluster:
    description: Kind cluster name for K8s.
    type: str
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

import json
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
    if instance.get("managedCluster"):
        result["managedCluster"] = True
    if instance.get("registry"):
        result["registry"] = instance["registry"]
    if instance.get("kindCluster"):
        result["kindCluster"] = instance["kindCluster"]
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


def run_module():
    module_args = dict(
        state=dict(type="str", default="query",
                   choices=["present", "absent", "query", "query_all",
                            "query_by_path",
                            "set_setting", "get_setting"]),
        setting_key=dict(type="str", required=False),
        setting_value=dict(type="str", required=False),
        id=dict(type="str", required=False),
        path=dict(type="str", required=False),
        orchestrator=dict(type="str", default="compose"),
        dev_mode=dict(type="bool", default=False),
        managed_cluster=dict(type="bool", default=False),
        registry=dict(type="str", required=False),
        kind_cluster=dict(type="str", required=False),
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

    state = module.params["state"]
    inst_id = module.params.get("id")
    inst_path = module.params.get("path")
    config_dir = get_config_dir(module.params.get("config_dir"))

    try:
        data = read_config(config_dir)
    except (json.JSONDecodeError, IOError) as e:
        module.fail_json(msg="Failed to read config: %s" % str(e))
        return

    instances = data.get("Instances", {})
    changed = False
    result = {"changed": False}

    if state == "query":
        if not inst_id and inst_path:
            inst_id, _ = find_by_path(instances, inst_path)
        if not inst_id:
            module.fail_json(msg="Instance ID is required for query (or provide path to auto-detect)")
            return
        if inst_id not in instances:
            module.fail_json(msg="Instance '%s' not found in registry" % inst_id)
            return
        result["instance"] = dict(instances[inst_id])
        result["instance"]["id"] = inst_id

    elif state == "query_all":
        filter_host = module.params.get("filter_host")
        if filter_host:
            # Match against the hostname portion of stored host field,
            # which may be in user@host format.
            def host_matches(stored, target):
                bare = stored.split("@", 1)[-1] if "@" in stored else stored
                return bare == target or stored == target

            result["instances"] = {
                k: v for k, v in instances.items()
                if host_matches(v.get("host", "localhost"), filter_host)
            }
        else:
            result["instances"] = instances

    elif state == "query_by_path":
        if not inst_path:
            module.fail_json(msg="path is required for query_by_path")
            return
        found_id, found_inst = find_by_path(instances, inst_path)
        if not found_id:
            module.fail_json(msg="No instance found for path '%s'" % inst_path)
            return
        result["instance"] = dict(found_inst)
        result["instance"]["id"] = found_id

    elif state == "present":
        if not inst_id:
            module.fail_json(msg="id is required when state=present")
            return
        if not inst_path:
            module.fail_json(msg="path is required when state=present")
            return

        new_instance = instance_to_dict({
            "id": inst_id,
            "path": os.path.abspath(inst_path),
            "orchestrator": module.params["orchestrator"],
            "host": module.params.get("host"),
            "devMode": module.params["dev_mode"],
            "managedCluster": module.params["managed_cluster"],
            "registry": module.params.get("registry"),
            "kindCluster": module.params.get("kind_cluster"),
            "buildFrom": module.params.get("build_from"),
            "buildArgs": module.params.get("build_args"),
            "dockerHost": module.params.get("docker_host"),
            "composeCommand": module.params.get("compose_command"),
            "inspectCommand": module.params.get("inspect_command"),
        })

        if inst_id in instances:
            if instances[inst_id] != new_instance:
                changed = True
                instances[inst_id] = new_instance
        else:
            changed = True
            instances[inst_id] = new_instance

        if changed and not module.check_mode:
            data["Instances"] = instances
            write_config(config_dir, data)

        result["changed"] = changed
        result["instance"] = new_instance

    elif state == "absent":
        if not inst_id:
            module.fail_json(msg="id is required when state=absent")
            return
        if inst_id in instances:
            changed = True
            if not module.check_mode:
                del instances[inst_id]
                data["Instances"] = instances
                write_config(config_dir, data)
        result["changed"] = changed

    elif state == "get_setting":
        setting_key = module.params.get("setting_key")
        if not setting_key:
            module.fail_json(msg="setting_key is required for get_setting")
            return
        settings = data.get("Settings", {})
        result["value"] = settings.get(setting_key)

    elif state == "set_setting":
        setting_key = module.params.get("setting_key")
        setting_value = module.params.get("setting_value")
        if not setting_key:
            module.fail_json(msg="setting_key is required for set_setting")
            return
        settings = data.get("Settings", {})
        old_value = settings.get(setting_key)
        if old_value != setting_value:
            changed = True
            if not module.check_mode:
                if setting_value is None:
                    settings.pop(setting_key, None)
                else:
                    settings[setting_key] = setting_value
                data["Settings"] = settings
                write_config(config_dir, data)
        result["changed"] = changed

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
