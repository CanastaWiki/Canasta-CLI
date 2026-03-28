#!/usr/bin/env python3
"""Validate that command definitions and playbooks stay in sync.

Checks:
1. Every command in command_definitions.yml has a corresponding playbook file
2. Every playbook file in playbooks/ has a corresponding command definition
3. All required fields are present in each command definition
4. Parameter types are valid

Usage:
    python scripts/validate_definitions.py
"""

import os
import sys

import yaml


VALID_TYPES = {"string", "path", "bool", "choice", "integer"}
REQUIRED_CMD_FIELDS = {"name", "description", "playbook", "parameters"}
REQUIRED_PARAM_FIELDS = {"name", "type", "description"}


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    definitions_path = os.path.join(repo_root, "meta", "command_definitions.yml")
    playbooks_dir = os.path.join(repo_root, "playbooks")

    with open(definitions_path) as f:
        data = yaml.safe_load(f)

    commands = data.get("commands", [])
    errors = []

    # Collect defined playbook names
    defined_playbooks = set()

    for i, cmd in enumerate(commands):
        prefix = "commands[%d] (%s)" % (i, cmd.get("name", "?"))

        # Check required command fields
        for field in REQUIRED_CMD_FIELDS:
            if field not in cmd:
                errors.append("%s: missing required field '%s'" % (prefix, field))

        name = cmd.get("name", "")
        playbook = cmd.get("playbook", "")

        if playbook:
            defined_playbooks.add(playbook)
            # Check playbook file exists
            playbook_path = os.path.join(playbooks_dir, playbook)
            if not os.path.exists(playbook_path):
                errors.append("%s: playbook '%s' not found at %s" % (prefix, playbook, playbook_path))

        # Check parameters
        for j, param in enumerate(cmd.get("parameters", [])):
            ppfx = "%s.parameters[%d] (%s)" % (prefix, j, param.get("name", "?"))
            for field in REQUIRED_PARAM_FIELDS:
                if field not in param:
                    errors.append("%s: missing required field '%s'" % (ppfx, field))
            ptype = param.get("type", "")
            if ptype and ptype not in VALID_TYPES:
                errors.append("%s: invalid type '%s' (must be one of %s)" % (
                    ppfx, ptype, ", ".join(sorted(VALID_TYPES))))
            if ptype == "choice" and not param.get("choices"):
                errors.append("%s: type 'choice' requires 'choices' list" % ppfx)

    # Check for orphan playbooks (files with no matching definition)
    if os.path.isdir(playbooks_dir):
        for fname in sorted(os.listdir(playbooks_dir)):
            if fname.endswith(".yml") and not fname.startswith("_") and fname not in defined_playbooks:
                errors.append("playbooks/%s: no matching command definition" % fname)

    # Check for duplicate command names
    names = [c.get("name", "") for c in commands]
    seen = set()
    for name in names:
        if name in seen:
            errors.append("Duplicate command name: '%s'" % name)
        seen.add(name)

    if errors:
        print("Validation FAILED with %d error(s):" % len(errors), file=sys.stderr)
        for e in errors:
            print("  - %s" % e, file=sys.stderr)
        sys.exit(1)
    else:
        print("Validation passed: %d commands, %d playbooks" % (
            len(commands), len(defined_playbooks)))


if __name__ == "__main__":
    main()
