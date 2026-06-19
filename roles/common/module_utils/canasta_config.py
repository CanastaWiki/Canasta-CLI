# -*- coding: utf-8 -*-
"""Shared registry-location and lookup helpers for Canasta.

The instance registry (conf.json) lives in a platform-specific per-user
config directory. Two callers need identical logic for *where* that file
is and *how* to find an instance by path:

  * the canasta_registry Ansible module, which reads and writes it, and
  * the canasta.py CLI wrapper, which must resolve an instance before
    handing off to ansible-playbook.

Pre-extraction each side carried its own copy; canasta.py's even had a
"Mirrors canasta_registry.py logic" comment. Two hand-synced copies of
the registry's location are how the CLI and the module silently end up
disagreeing about where conf.json is. This module is the single source.

The canasta_* Ansible modules import it via
`from ansible.module_utils.canasta_config import ...` (the role's
module_utils dir is on the loader path via ansible.cfg). canasta.py
imports it directly off the same directory. Pure stdlib so both import
paths work without Ansible loaded.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import os
import platform
import pwd


CONFIG_FILENAME = "conf.json"


def is_root():
    """Return True when running as root."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name == "root"
    except (KeyError, AttributeError):
        return os.getuid() == 0


def get_config_dir(override=None):
    """Return the directory holding conf.json, by convention:
    - explicit override / $CANASTA_CONFIG_DIR (test isolation) win
    - root: /etc/canasta
    - macOS: ~/Library/Application Support/canasta
    - Linux: $XDG_CONFIG_HOME/canasta or ~/.config/canasta
    """
    if override:
        return override
    env_dir = os.environ.get("CANASTA_CONFIG_DIR")
    if env_dir:
        return env_dir
    if is_root():
        return "/etc/canasta"
    if platform.system() == "Darwin":
        config_home = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support"
        )
    else:
        config_home = os.environ.get(
            "XDG_CONFIG_HOME",
            os.path.join(os.path.expanduser("~"), ".config"),
        )
    return os.path.join(config_home, "canasta")


def config_path(config_dir):
    """Return the full path to conf.json inside config_dir."""
    return os.path.join(config_dir, CONFIG_FILENAME)


def read_config(config_dir):
    """Read and return the registry dict, normalizing the schema.

    A missing or empty file yields {"Instances": {}}. The legacy
    "Installations" key is migrated to "Instances".
    """
    path = config_path(config_dir)
    if not os.path.exists(path):
        return {"Instances": {}}
    with open(path, "r") as f:
        content = f.read().strip()
    if not content:
        return {"Instances": {}}
    data = json.loads(content)
    if "Installations" in data and "Instances" not in data:
        data["Instances"] = data.pop("Installations")
    if "Instances" not in data:
        data["Instances"] = {}
    return data


def write_config(config_dir, data):
    """Write the registry dict to conf.json, creating config_dir."""
    os.makedirs(config_dir, mode=0o755, exist_ok=True)
    path = config_path(config_dir)
    with open(path, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")


def find_by_path(instances, search_path):
    """Walk up from search_path; return (id, instance) of the registered
    instance whose `path` matches, or (None, None)."""
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
