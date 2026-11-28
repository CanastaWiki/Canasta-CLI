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

import contextlib
import fcntl
import json
import os
import platform
import pwd
import tempfile


CONFIG_FILENAME = "conf.json"
LOCK_FILENAME = "conf.json.lock"

# Registry entries from 4.x Kubernetes instances, refused on read. Older
# entries spell it `k8s`.
K8S_ORCHESTRATORS = ("kubernetes", "k8s")


def is_k8s_orchestrator(orchestrator):
    """Return True when `orchestrator` names Kubernetes."""
    return orchestrator in K8S_ORCHESTRATORS


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
    return read_config_file(config_path(config_dir))


def read_config_file(path):
    """read_config for a registry file named by its path."""
    if not os.path.exists(path):
        return {"Instances": {}}
    with open(path, "r") as f:
        content = f.read().strip()
    if not content:
        return {"Instances": {}}
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("%s: top level is not a JSON object" % path)
    if "Installations" in data and "Instances" not in data:
        data["Instances"] = data.pop("Installations")
    if "Instances" not in data:
        data["Instances"] = {}
    return data


def write_config(config_dir, data):
    """Write the registry dict to conf.json, creating config_dir.

    Written atomically (temp file in the same dir + os.replace) so a crash
    mid-write can't leave a truncated conf.json — it is the single source of
    truth for every registered instance."""
    os.makedirs(config_dir, mode=0o755, exist_ok=True)
    path = config_path(config_dir)
    fd, tmp = tempfile.mkstemp(dir=config_dir, prefix=".conf.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def locked(config_dir):
    """Hold the registry's lock across a read, change and write of it, so
    two commands registering at once cannot drop each other's change.
    Where the lock file cannot be opened, the write that follows fails on
    its own, so this goes on without it."""
    try:
        os.makedirs(config_dir, mode=0o755, exist_ok=True)
        fd = os.open(os.path.join(config_dir, LOCK_FILENAME),
                     os.O_RDONLY | os.O_CREAT, 0o644)
    except OSError:
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


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
