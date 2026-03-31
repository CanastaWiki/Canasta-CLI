#!/usr/bin/env python3
"""Canasta CLI wrapper -- translates CLI invocations into ansible-playbook calls.

Reads command definitions from meta/command_definitions.yml and builds
argparse subcommands with proper type-aware flag parsing. This replaces
the bash wrapper script with Cobra-equivalent argument handling.
"""

import argparse
import os
import shutil
import subprocess
import sys

import yaml


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFINITIONS_PATH = os.path.join(SCRIPT_DIR, "meta", "command_definitions.yml")
CANASTA_YML = os.path.join(SCRIPT_DIR, "canasta.yml")

# Commands that have subcommands (e.g., "config get" -> "config_get")
SUBCOMMAND_GROUPS = {
    "config": ["get", "set", "unset"],
    "extension": ["list", "enable", "disable"],
    "skin": ["list", "enable", "disable"],
    "maintenance": ["update", "script", "extension", "exec"],
    "devmode": ["enable", "disable"],
    "sitemap": ["generate", "remove"],
    "backup": [
        "init", "list", "create", "restore", "delete",
        "unlock", "files", "check", "diff", "purge",
    ],
    "gitops": [
        "init", "join", "add", "rm", "push", "pull",
        "status", "diff", "fix-submodules",
    ],
}

# Nested subcommand groups (backup schedule set|list|remove)
NESTED_SUBCOMMAND_GROUPS = {
    "backup": {
        "schedule": ["set", "list", "remove"],
    },
}


def load_definitions():
    """Load command definitions from YAML."""
    with open(DEFINITIONS_PATH) as f:
        return yaml.safe_load(f)


def find_ansible_playbook():
    """Find the ansible-playbook executable."""
    venv_path = os.path.join(SCRIPT_DIR, ".venv", "bin", "ansible-playbook")
    if os.path.isfile(venv_path) and os.access(venv_path, os.X_OK):
        return venv_path
    system_path = shutil.which("ansible-playbook")
    if system_path:
        return system_path
    print(
        "Error: ansible-playbook not found. Install Ansible or run:\n"
        "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)


def internal_name(display_name):
    """Convert display command name to internal name (e.g., 'fix-submodules' -> 'fix_submodules')."""
    return display_name.replace("-", "_")


def display_name(internal):
    """Convert internal name to display name (e.g., 'fix_submodules' -> 'fix-submodules')."""
    return internal.replace("_", "-")


def add_params_to_parser(parser, params):
    """Add command parameters to an argparse parser based on definitions."""
    for param in params:
        name = param["name"]
        ptype = param.get("type", "string")
        short = param.get("short")
        desc = param.get("description", "")
        default = param.get("default")
        required = param.get("required", False)
        positional = param.get("positional", False)

        if param.get("sensitive") and "auto-generated" not in desc:
            desc += " (auto-generated if not provided)"

        flag_name = "--" + name.replace("_", "-")
        flags = [flag_name]
        if short:
            flags.insert(0, "-" + short)

        if positional:
            # exec_args/script_args consume all remaining args
            # (e.g., "maintenance exec -i x php -v" -> "php -v")
            if name in ("exec_args", "script_args"):
                parser.add_argument(
                    name,
                    nargs=argparse.REMAINDER,
                    default=default,
                    help=desc,
                    metavar=name.upper(),
                )
            else:
                parser.add_argument(
                    name,
                    nargs="?",
                    default=default,
                    help=desc,
                    metavar=name.upper(),
                )
        elif ptype == "bool":
            parser.add_argument(
                *flags,
                action="store_true",
                default=False,
                help=desc,
                dest=name,
            )
        elif ptype == "choice":
            choices = param.get("choices", [])
            parser.add_argument(
                *flags,
                choices=choices,
                default=default,
                help=desc,
                dest=name,
            )
        else:
            parser.add_argument(
                *flags,
                default=default,
                required=required,
                help=desc,
                dest=name,
            )


def build_parser(data):
    """Build the full argparse parser from command definitions."""
    parser = argparse.ArgumentParser(
        prog="canasta",
        description="Canasta MediaWiki management tool (Ansible edition).",
    )

    # Global flags
    parser.add_argument(
        "--host", "-H",
        default=None,
        help="Target host (default: localhost)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Index commands by internal name for lookup
    cmd_index = {c["name"]: c for c in data["commands"]}

    # Top-level commands (no subcommands)
    grouped_prefixes = set(SUBCOMMAND_GROUPS.keys())
    top_level_cmds = []
    for cmd in data["commands"]:
        name = cmd["name"]
        # Skip commands that belong to a subcommand group
        prefix = name.split("_")[0] if "_" in name else None
        if prefix in grouped_prefixes:
            continue
        top_level_cmds.append(cmd)

    for cmd in top_level_cmds:
        name = cmd["name"]
        sp = subparsers.add_parser(
            display_name(name),
            help=cmd.get("description", ""),
            description=cmd.get("long_description", cmd.get("description", "")),
        )
        add_params_to_parser(sp, cmd.get("parameters", []))

    # Subcommand groups
    for group, subcmds in SUBCOMMAND_GROUPS.items():
        group_parser = subparsers.add_parser(
            group,
            help="Manage %s" % group,
        )
        group_subs = group_parser.add_subparsers(
            dest="subcommand",
            help="%s subcommand" % group,
        )

        for sub in subcmds:
            internal = "%s_%s" % (group, internal_name(sub))
            cmd_def = cmd_index.get(internal)
            if not cmd_def:
                continue
            sub_parser = group_subs.add_parser(
                sub,
                help=cmd_def.get("description", ""),
                description=cmd_def.get(
                    "long_description", cmd_def.get("description", "")
                ),
            )
            add_params_to_parser(sub_parser, cmd_def.get("parameters", []))

        # Handle nested subcommand groups (backup schedule)
        nested = NESTED_SUBCOMMAND_GROUPS.get(group, {})
        for nested_group, nested_subcmds in nested.items():
            nested_parser = group_subs.add_parser(
                nested_group,
                help="Manage %s %s" % (group, nested_group),
            )
            nested_subs = nested_parser.add_subparsers(
                dest="nested_subcommand",
                help="%s %s subcommand" % (group, nested_group),
            )
            for nsub in nested_subcmds:
                internal = "%s_%s_%s" % (
                    group, nested_group, internal_name(nsub)
                )
                cmd_def = cmd_index.get(internal)
                if not cmd_def:
                    continue
                nsub_parser = nested_subs.add_parser(
                    nsub,
                    help=cmd_def.get("description", ""),
                    description=cmd_def.get(
                        "long_description",
                        cmd_def.get("description", ""),
                    ),
                )
                add_params_to_parser(
                    nsub_parser, cmd_def.get("parameters", [])
                )

    return parser


def resolve_command_name(args):
    """Resolve the internal command name from parsed args."""
    cmd = args.command
    if not cmd:
        return None

    cmd = internal_name(cmd)
    sub = getattr(args, "subcommand", None)
    if sub:
        sub = internal_name(sub)
        nested = getattr(args, "nested_subcommand", None)
        if nested:
            nested = internal_name(nested)
            return "%s_%s_%s" % (cmd, sub, nested)
        return "%s_%s" % (cmd, sub)
    return cmd


def build_ansible_args(ansible_playbook, command_name, args, data):
    """Build the ansible-playbook command line."""
    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(command_name, {})
    params = cmd_def.get("parameters", [])
    param_names = {p["name"] for p in params}

    ansible_args = [
        ansible_playbook,
        CANASTA_YML,
        "-e", "command=%s" % command_name,
    ]

    # Global flags
    if args.host:
        ansible_args.extend([
            "-e", "target_host=%s" % args.host,
            "--limit", args.host,
        ])

    if args.verbose:
        ansible_args.extend(["-e", "verbose=true"])
    else:
        os.environ["ANSIBLE_STDOUT_CALLBACK"] = "canasta_minimal"

    # Command parameters
    for param in params:
        name = param["name"]
        value = getattr(args, name, None)
        if value is None:
            continue
        # REMAINDER args come as a list, join into string
        if isinstance(value, list):
            value = " ".join(value)
            if not value:
                continue
        ptype = param.get("type", "string")
        if ptype == "bool":
            if value:
                ansible_args.extend(["-e", "%s=true" % name])
        else:
            # Quote the value to prevent ansible-playbook from
            # interpreting flags within it (e.g., "php -v")
            ansible_args.extend(["-e", "%s=%s" % (name, value)]
                                if " " not in str(value)
                                else ["-e", '%s="%s"' % (name, value)])

    return ansible_args


def main():
    data = load_definitions()
    parser = build_parser(data)

    # Extract global flags (--verbose/-v, --host/-H) only from args
    # BEFORE the subcommand. This prevents "-v" in
    # "maintenance exec -i x php -v" from being consumed as --verbose.
    raw_args = sys.argv[1:]
    pre_cmd = []
    post_cmd = []
    found_cmd = False
    # Known top-level commands
    cmd_names = {c["name"].split("_")[0] for c in data["commands"]}
    cmd_names |= {display_name(n) for n in cmd_names}
    for arg in raw_args:
        if not found_cmd and arg in cmd_names:
            found_cmd = True
        if found_cmd:
            post_cmd.append(arg)
        else:
            pre_cmd.append(arg)

    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument("--host", "-H", default=None)
    global_parser.add_argument("--verbose", "-v", action="store_true",
                               default=False)
    global_args, pre_remaining = global_parser.parse_known_args(pre_cmd)

    # Recombine: unused pre-command args + all post-command args
    remaining = pre_remaining + post_cmd

    # Handle -- separator for pass-through args
    passthrough = ""
    if "--" in remaining:
        idx = remaining.index("--")
        passthrough = " ".join(remaining[idx + 1:])
        remaining = remaining[:idx]

    # Parse with the full parser (subcommands + flags)
    args = parser.parse_args(remaining)

    # Merge global flags into args
    args.host = global_args.host or args.host
    args.verbose = global_args.verbose or args.verbose

    # Inject passthrough args (after --) into the positional parameter
    if passthrough:
        # Find the positional parameter for this command
        positional_names = {
            "maintenance_exec": "exec_args",
            "maintenance_script": "script_args",
            "maintenance_extension": "script_args",
            "config_get": "key",
            "config_set": "settings",
            "config_unset": "keys",
            "extension_enable": "extensions",
            "extension_disable": "extensions",
            "skin_enable": "skins",
            "skin_disable": "skins",
            "backup_schedule_set": "cron_expression",
        }
        cmd_name = resolve_command_name(args) if args.command else None
        pos_name = positional_names.get(cmd_name)
        if pos_name:
            setattr(args, pos_name, passthrough)
        else:
            # Generic fallback
            args.args = passthrough

    if not args.command:
        parser.print_help()
        sys.exit(1)

    command_name = resolve_command_name(args)
    if not command_name:
        parser.print_help()
        sys.exit(1)

    # Verify command exists in definitions
    cmd_names = {c["name"] for c in data["commands"]}
    if command_name not in cmd_names:
        print("Unknown command: %s" % command_name, file=sys.stderr)
        sys.exit(1)

    ansible_playbook = find_ansible_playbook()
    ansible_args = build_ansible_args(
        ansible_playbook, command_name, args, data
    )

    os.execvp(ansible_args[0], ansible_args)


if __name__ == "__main__":
    main()
