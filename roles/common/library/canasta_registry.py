#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for managing the Canasta instance registry (conf.json).

Replaces the Go internal/config package. Provides CRUD operations on the
instance registry stored at conf.json, with the same directory resolution
logic as the Go CLI.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_registry
short_description: Manage the Canasta instance registry
description:
  - Read, add, update, and remove Canasta instances from the local registry (conf.json).
  - Compatible with the Go CLI's registry format.
options:
  state:
    description: Desired state of the instance in the registry.
    type: str
    choices: [present, absent, query, query_all, query_by_path, cleanup]
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
  config_dir:
    description: Override the config directory (instead of auto-detection).
    type: str
"""

import json
import os
import pwd

from ansible.module_utils.basic import AnsibleModule


CONFIG_FILENAME = "conf.json"


def get_config_dir(override=None):
    """Determine the config directory using the same priority as the Go CLI."""
    if override:
        return override
    env_dir = os.environ.get("CANASTA_CONFIG_DIR")
    if env_dir:
        return env_dir
    if is_root():
        return "/etc/canasta"
    config_home = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(config_home, "canasta")


def is_root():
    """Check if running as root."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name == "root"
    except (KeyError, AttributeError):
        return os.getuid() == 0


def config_path(config_dir):
    """Return the full path to conf.json."""
    return os.path.join(config_dir, CONFIG_FILENAME)


def read_config(config_dir):
    """Read and return the registry config, creating it if needed."""
    path = config_path(config_dir)
    if not os.path.exists(path):
        return {"Instances": {}}
    with open(path, "r") as f:
        content = f.read().strip()
    if not content:
        return {"Instances": {}}
    data = json.loads(content)
    # Migrate legacy "Installations" key
    if "Installations" in data and "Instances" not in data:
        data["Instances"] = data.pop("Installations")
    if "Instances" not in data:
        data["Instances"] = {}
    return data


def write_config(config_dir, data):
    """Write the registry config to conf.json."""
    os.makedirs(config_dir, mode=0o755, exist_ok=True)
    path = config_path(config_dir)
    with open(path, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")


def instance_to_dict(instance):
    """Convert an instance dict to the JSON-serializable format (matching Go struct tags)."""
    result = {
        "id": instance.get("id", ""),
        "path": instance.get("path", ""),
        "orchestrator": instance.get("orchestrator", "compose"),
    }
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
    return result


def find_by_path(instances, search_path):
    """Walk up from search_path to find a matching instance (mirrors Go GetCanastaID)."""
    search_path = os.path.abspath(search_path)
    while True:
        for inst_id, inst in instances.items():
            if os.path.abspath(inst.get("path", "")) == search_path:
                return inst_id, inst
        parent = os.path.dirname(search_path)
        if parent == search_path:
            break
        search_path = parent
    return None, None


def run_module():
    module_args = dict(
        state=dict(type="str", default="query",
                   choices=["present", "absent", "query", "query_all", "query_by_path", "cleanup"]),
        id=dict(type="str", required=False),
        path=dict(type="str", required=False),
        orchestrator=dict(type="str", default="compose"),
        dev_mode=dict(type="bool", default=False),
        managed_cluster=dict(type="bool", default=False),
        registry=dict(type="str", required=False),
        kind_cluster=dict(type="str", required=False),
        build_from=dict(type="str", required=False),
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
        result["instance"] = instances[inst_id]
        result["instance"]["id"] = inst_id

    elif state == "query_all":
        result["instances"] = instances

    elif state == "query_by_path":
        if not inst_path:
            module.fail_json(msg="path is required for query_by_path")
            return
        found_id, found_inst = find_by_path(instances, inst_path)
        if not found_id:
            module.fail_json(msg="No instance found for path '%s'" % inst_path)
            return
        result["instance"] = found_inst
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
            "devMode": module.params["dev_mode"],
            "managedCluster": module.params["managed_cluster"],
            "registry": module.params.get("registry"),
            "kindCluster": module.params.get("kind_cluster"),
            "buildFrom": module.params.get("build_from"),
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

    elif state == "cleanup":
        to_remove = []
        for iid, inst in instances.items():
            if not os.path.isdir(inst.get("path", "")):
                to_remove.append(iid)
        if to_remove:
            changed = True
            if not module.check_mode:
                for iid in to_remove:
                    del instances[iid]
                data["Instances"] = instances
                write_config(config_dir, data)
        result["changed"] = changed
        result["removed"] = to_remove

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
