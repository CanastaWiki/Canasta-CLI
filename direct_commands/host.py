"""host list / add / remove commands."""

import os
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


@register("host_list")
def cmd_host_list(args):
    data = _helpers._read_hosts_yml()
    if data is None:
        print("No hosts configured (no file at %s)." % _helpers._hosts_yml_path())
        return 0

    hosts = data.get("all", {}).get("hosts", {})
    if not hosts:
        print("No hosts configured.")
        return 0

    for name, entry in hosts.items():
        print(name)
        for key, value in entry.items():
            print("  %s: %s" % (key, value))
    return 0


@register("host_add")
def cmd_host_add(args):
    ssh_dest = getattr(args, "ssh", "")
    host_name = getattr(args, "host_name", "")
    python_path = getattr(args, "python", None)

    if "@" in ssh_dest:
        ssh_user, ssh_host = ssh_dest.split("@", 1)
    else:
        ssh_user, ssh_host = "", ssh_dest

    data = _helpers._read_hosts_yml()
    if data is None:
        data = {"all": {"hosts": {}}}

    entry = {"ansible_host": ssh_host}
    if ssh_user:
        entry["ansible_user"] = ssh_user
    if python_path:
        entry["ansible_python_interpreter"] = python_path

    data.setdefault("all", {}).setdefault("hosts", {})[host_name] = entry
    _helpers._write_hosts_yml(data)
    print("Host '%s' saved to %s" % (host_name, _helpers._hosts_yml_path()))
    return 0


@register("host_remove")
def cmd_host_remove(args):
    host_name = getattr(args, "host_name", "")
    data = _helpers._read_hosts_yml()

    if data is None:
        print("No hosts.yml found at %s" % _helpers._hosts_yml_path(), file=sys.stderr)
        return 1

    hosts = data.get("all", {}).get("hosts", {})
    if host_name not in hosts:
        print("Host '%s' not found in %s" % (host_name, _helpers._hosts_yml_path()), file=sys.stderr)
        return 1

    del hosts[host_name]
    _helpers._write_hosts_yml(data)
    print("Host '%s' removed from %s" % (host_name, _helpers._hosts_yml_path()))
    return 0
