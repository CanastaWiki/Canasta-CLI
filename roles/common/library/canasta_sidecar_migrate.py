#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module: migrate legacy docker-compose.override.yml sidecars.

Translates the override's non-core services into config/sidecars.yaml (the
orchestrator-agnostic schema) and removes them from the override, so the
migrated sidecar isn't shadowed by a left-behind override entry. Core-stack
overrides and services it can't fully model stay in the override and are
reported. dry_run reports the plan without writing.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_sidecar_migrate
short_description: Migrate docker-compose.override.yml sidecars to sidecars.yaml
description:
  - Translate non-core override services into config/sidecars.yaml and remove
    them from the override (move, not copy).
options:
  instance_path:
    description: Path to the Canasta instance directory.
    type: str
    required: true
  dry_run:
    description: Report the plan without writing any files.
    type: bool
    default: false
  orchestrator:
    description:
      - The instance orchestrator. On Kubernetes, a migrated sidecar with a
        plain C(${VAR}) reference to a secret variable is refused.
    type: str
    choices: [compose, k8s, kubernetes]
    default: compose
  secret_key_regex:
    description:
      - The secret classifier (canasta_secret_key_regex); required on
        Kubernetes.
    type: str
"""

import os

import yaml

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_override_migrate import plan_migration
from ansible.module_utils.canasta_sidecar_render import (
    secret_classifier,
    validate_k8s_secret_refs,
)


def _read_yaml(path):
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return yaml.safe_load(handle)


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            instance_path=dict(type="str", required=True),
            dry_run=dict(type="bool", default=False),
            orchestrator=dict(type="str", default="compose",
                              choices=["compose", "k8s", "kubernetes"]),
            secret_key_regex=dict(type="str", required=False),
        ),
        supports_check_mode=True,
    )

    instance_path = module.params["instance_path"]
    dry_run = module.params.get("dry_run") or False
    override_path = os.path.join(instance_path, "docker-compose.override.yml")
    sidecars_path = os.path.join(instance_path, "config", "sidecars.yaml")

    override = _read_yaml(override_path)
    if not override or not (override.get("services")):
        module.exit_json(changed=False, migrated=[], unmigrated=[],
                         assumptions=[], dry_run=dry_run,
                         message="No docker-compose.override.yml services to migrate.")
        return

    existing = (_read_yaml(sidecars_path) or {}).get("sidecars", []) or []
    existing_names = [s.get("name") for s in existing]
    plan = plan_migration(override, existing_names=existing_names)

    if (module.params.get("orchestrator") or "compose") != "compose":
        regex = module.params.get("secret_key_regex")
        if not regex:
            module.fail_json(
                msg="secret_key_regex is required on Kubernetes")
            return
        error = validate_k8s_secret_refs(
            plan["sidecars"], secret_classifier(instance_path, regex))
        if error:
            module.fail_json(msg=error)
            return

    result = dict(
        migrated=plan["migrated"],
        unmigrated=plan["skipped"],
        assumptions=plan["assumptions"],
        dry_run=dry_run,
        changed=bool(plan["migrated"]) and not dry_run,
    )

    if not plan["migrated"] or dry_run:
        module.exit_json(**result)
        return

    # Write merged sidecars.yaml.
    merged = existing + plan["sidecars"]
    os.makedirs(os.path.dirname(sidecars_path), mode=0o755, exist_ok=True)
    with open(sidecars_path, "w") as handle:
        yaml.dump({"sidecars": merged}, handle,
                  default_flow_style=False, sort_keys=False)

    # Rewrite (or remove) the override. Remove only when nothing meaningful
    # remains — no services and no other top-level keys besides version.
    remaining = plan["remaining_override"]
    meaningful = [k for k in remaining
                  if k not in ("version", "services")]
    if not remaining.get("services") and not meaningful:
        os.remove(override_path)
    else:
        with open(override_path, "w") as handle:
            yaml.dump(remaining, handle,
                      default_flow_style=False, sort_keys=False)

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
