#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module: manage an instance's maintenance schedules.

Edits config/schedules.yml and keeps the instance's crontab block in step
with it, on the host the module runs on.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_schedules
short_description: Manage an instance's maintenance schedules
description:
  - Read, add, or remove entries in config/schedules.yml, write the
    instance's crontab block from it, clear that block, or report status.
options:
  action:
    description: What to do.
    type: str
    required: true
    choices: [read, add, remove, sync, clear, status]
  instance_id:
    description: The instance ID.
    type: str
    required: true
  instance_path:
    description: Path to the instance directory. Not needed for clear.
    type: str
  name:
    description: Schedule name, for add and remove.
    type: str
  cron:
    description: Five-field cron expression, for add.
    type: str
  service:
    description: Service whose container runs the command, for add.
    type: str
    default: web
  command:
    description: Shell command run inside the container, for add.
    type: str
  hosts:
    description: Gitops host names the schedule is limited to, for add.
    type: list
    elements: str
  canasta_bin:
    description: The canasta command cron runs, for sync.
    type: str
    default: ""
  cron_env:
    description: Environment assignments prefixed to the cron job, for sync.
    type: str
    default: ""
  crontab_file:
    description:
      - Edit or read this file instead of the live crontab (the
        containerized CLI's host crontab hand-off).
    type: str
    default: ""
"""

import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_atomic_write import replace_file
from ansible.module_utils.canasta_schedule import (
    SCHEDULES_FILE,
    STATE_DIR,
    cron_line,
    dump,
    is_active,
    parse,
    render_table,
    rewrite_crontab,
    strip_instance,
    validate_entry,
)

import yaml


def _schedules_path(instance_path):
    return os.path.join(instance_path, SCHEDULES_FILE)


def _read_schedules(module, instance_path):
    path = _schedules_path(instance_path)
    if not os.path.exists(path):
        return {}
    with open(path) as handle:
        text = handle.read()
    try:
        return parse(text)
    except (ValueError, yaml.YAMLError) as exc:
        module.fail_json(msg=str(exc))


def _write_schedules(instance_path, schedules):
    path = _schedules_path(instance_path)
    if not schedules:
        if os.path.exists(path):
            os.remove(path)
        return
    replace_file(path, dump(schedules))


def _gitops_hostname(instance_path):
    path = os.path.join(instance_path or "", ".gitops-host")
    if not instance_path or not os.path.exists(path):
        return ""
    with open(path) as handle:
        return handle.read().strip()


def _read_crontab(module, crontab_file, required):
    """(text, available). Missing crontab binary fails only if required."""
    if crontab_file:
        if not os.path.exists(crontab_file):
            return "", True
        with open(crontab_file) as handle:
            return handle.read(), True
    binary = module.get_bin_path("crontab")
    if not binary:
        if required:
            module.fail_json(msg=(
                "The 'crontab' command is not available on this host. "
                "Install the cron package (e.g. 'apt install cron') to use "
                "schedules."))
        return "", False
    rc, out, _ = module.run_command([binary, "-l"])
    # rc 1 means no crontab yet.
    return (out if rc == 0 else ""), True


def _write_crontab(module, crontab_file, text):
    if crontab_file:
        replace_file(crontab_file, text)
        return
    binary = module.get_bin_path("crontab", required=True)
    rc, _, err = module.run_command([binary, "-"], data=text,
                                    binary_data=True)
    if rc != 0:
        module.fail_json(msg="Failed to install the crontab: %s" % err)


def _read_states(instance_path):
    states = {}
    state_dir = os.path.join(instance_path, STATE_DIR)
    if not os.path.isdir(state_dir):
        return states
    for entry in sorted(os.listdir(state_dir)):
        if not entry.endswith(".yml"):
            continue
        try:
            with open(os.path.join(state_dir, entry)) as handle:
                data = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            states[entry[:-len(".yml")]] = data
    return states


def _sync(module, p):
    schedules = _read_schedules(module, p["instance_path"])
    hostname = _gitops_hostname(p["instance_path"])
    active = sorted(n for n, e in schedules.items() if is_active(e, hostname))
    inactive = sorted(set(schedules) - set(active))
    if active and not p["canasta_bin"]:
        module.fail_json(msg=(
            "No 'canasta' executable found on this host. Scheduled jobs run "
            "'canasta schedule run' here, so install it first (e.g. "
            "'canasta install canasta -H <host>')."))
    lines = [cron_line(n, schedules[n], p["instance_id"], p["instance_path"],
                       p["canasta_bin"], p["cron_env"]) for n in active]
    text, _ = _read_crontab(module, p["crontab_file"], required=bool(active))
    new = rewrite_crontab(text, p["instance_id"], lines)
    changed = new.strip() != text.strip()
    if changed and not module.check_mode:
        _write_crontab(module, p["crontab_file"], new)
    return dict(changed=changed, active=active, inactive=inactive,
                gitops_hostname=hostname)


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            action=dict(type="str", required=True,
                        choices=["read", "add", "remove", "sync", "clear",
                                 "status"]),
            instance_id=dict(type="str", required=True),
            instance_path=dict(type="str"),
            name=dict(type="str"),
            cron=dict(type="str"),
            service=dict(type="str", default="web"),
            command=dict(type="str"),
            hosts=dict(type="list", elements="str"),
            canasta_bin=dict(type="str", default=""),
            cron_env=dict(type="str", default=""),
            crontab_file=dict(type="str", default=""),
        ),
        required_if=[
            ("action", "read", ["instance_path"]),
            ("action", "add", ["instance_path", "name", "cron", "command"]),
            ("action", "remove", ["instance_path", "name"]),
            ("action", "sync", ["instance_path"]),
            ("action", "status", ["instance_path"]),
        ],
        supports_check_mode=True,
    )
    p = module.params
    action = p["action"]

    if action == "read":
        module.exit_json(changed=False,
                         schedules=_read_schedules(module, p["instance_path"]))

    if action == "add":
        schedules = _read_schedules(module, p["instance_path"])
        name = p["name"]
        if name in schedules:
            module.fail_json(msg=(
                "Schedule '%s' already exists. Remove it first with "
                "'canasta schedule remove --name %s', then add it again."
                % (name, name)))
        entry = {"cron": " ".join(p["cron"].split()), "service": p["service"],
                 "command": p["command"]}
        if p["hosts"]:
            entry["hosts"] = p["hosts"]
        errors = validate_entry(name, entry)
        if errors:
            module.fail_json(msg="; ".join(errors))
        schedules[name] = entry
        if not module.check_mode:
            _write_schedules(p["instance_path"], schedules)
        module.exit_json(changed=True, schedules=schedules,
                         active=is_active(entry, _gitops_hostname(
                             p["instance_path"])))

    if action == "remove":
        schedules = _read_schedules(module, p["instance_path"])
        if p["name"] not in schedules:
            module.fail_json(msg="No schedule named '%s'." % p["name"])
        del schedules[p["name"]]
        if not module.check_mode:
            _write_schedules(p["instance_path"], schedules)
        module.exit_json(changed=True, schedules=schedules)

    if action == "sync":
        module.exit_json(**_sync(module, p))

    if action == "clear":
        text, available = _read_crontab(module, p["crontab_file"], False)
        if not available:
            module.exit_json(changed=False)
        kept = strip_instance(text, p["instance_id"])
        new = ("\n".join(kept) + "\n") if kept else ""
        changed = new.strip() != text.strip()
        if changed and not module.check_mode:
            _write_crontab(module, p["crontab_file"], new)
        module.exit_json(changed=changed)

    # status
    schedules = _read_schedules(module, p["instance_path"])
    text, _ = _read_crontab(module, p["crontab_file"], False)
    hostname = _gitops_hostname(p["instance_path"])
    module.exit_json(
        changed=False, schedules=schedules,
        table=render_table(schedules, text, _read_states(p["instance_path"]),
                           p["instance_id"], hostname))


def main():
    run_module()


if __name__ == "__main__":
    main()
