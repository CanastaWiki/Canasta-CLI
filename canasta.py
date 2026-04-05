#!/usr/bin/env python3
"""Canasta CLI wrapper -- translates CLI invocations into ansible-playbook calls.

Reads command definitions from meta/command_definitions.yml and builds
argparse subcommands with proper type-aware flag parsing. This replaces
the bash wrapper script with Cobra-equivalent argument handling.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

import yaml


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFINITIONS_PATH = os.path.join(SCRIPT_DIR, "meta", "command_definitions.yml")
CANASTA_YML = os.path.join(SCRIPT_DIR, "canasta.yml")
ANSIBLE_CFG = os.path.join(SCRIPT_DIR, "ansible.cfg")

# Ensure Ansible uses the repo's config regardless of working directory
os.environ.setdefault("ANSIBLE_CONFIG", ANSIBLE_CFG)

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
        "status", "diff", "fix-submodules", "sync",
    ],
    "storage": ["setup"],
    "host": ["add", "remove", "list"],
}

# Nested subcommand groups (backup schedule set|list|remove)
NESTED_SUBCOMMAND_GROUPS = {
    "backup": {
        "schedule": ["set", "list", "remove"],
    },
    "storage": {
        "setup": ["nfs", "efs"],
    },
}


def load_definitions():
    """Load command definitions from YAML."""
    try:
        with open(DEFINITIONS_PATH) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print("Error: Command definitions not found at %s" % DEFINITIONS_PATH,
              file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print("Error: Failed to parse %s: %s" % (DEFINITIONS_PATH, e),
              file=sys.stderr)
        sys.exit(1)


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

        long_name = param.get("long", name)
        flag_name = "--" + long_name.replace("_", "-")
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


def get_config_dir():
    """Return the user config directory (where conf.json lives).

    Mirrors canasta_registry.py logic.
    """
    override = os.environ.get("CANASTA_CONFIG_DIR")
    if override:
        return override
    if os.geteuid() == 0:
        return "/etc/canasta"
    import platform
    if platform.system() == "Darwin":
        base = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support"
        )
    else:
        base = os.environ.get(
            "XDG_CONFIG_HOME",
            os.path.join(os.path.expanduser("~"), ".config"),
        )
    return os.path.join(base, "canasta")


def get_config_file_path():
    """Return the path to conf.json."""
    return os.path.join(get_config_dir(), "conf.json")


def build_parser(data):
    """Build the full argparse parser from command definitions."""
    parser = argparse.ArgumentParser(
        prog="canasta",
        description="Canasta MediaWiki management tool (Ansible edition).",
        epilog="Config file: %s" % get_config_file_path(),
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

    subparsers = parser.add_subparsers(
        dest="command",
        title="Commands",
        metavar="",
    )

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
    """Build the ansible-playbook command line.

    Extra vars are passed via a temp JSON file (-e @file) to avoid
    Jinja2 injection and shell quoting issues.
    """
    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(command_name, {})
    params = cmd_def.get("parameters", [])

    # Build extra vars as a dict, written to a JSON file
    extra_vars = {"command": command_name}

    # Global flags
    if args.host:
        extra_vars["target_host"] = args.host

    if args.verbose:
        extra_vars["verbose"] = "true"
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
                extra_vars[name] = "true"
        else:
            extra_vars[name] = str(value)

    # Write vars to temp file (bypasses Jinja2 interpolation)
    vars_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="canasta-vars-",
        delete=False,
    )
    json.dump(extra_vars, vars_file)
    vars_file.close()
    os.chmod(vars_file.name, 0o600)

    ansible_args = [
        ansible_playbook,
        CANASTA_YML,
        "-e", "@%s" % vars_file.name,
    ]

    if args.host:
        # Parse user@host shorthand.
        host_spec = args.host
        ssh_user = None
        if "@" in host_spec:
            ssh_user, host_spec = host_spec.split("@", 1)

        # Update target_host in the vars file to use just the hostname,
        # not the user@host form (the hosts.yml or inline inventory
        # entry is keyed on hostname alone).
        import json as _json
        with open(vars_file.name, "r") as f:
            _v = _json.load(f)
        _v["target_host"] = host_spec
        with open(vars_file.name, "w") as f:
            _json.dump(_v, f)

        # Inline inventory fallback — used if the host isn't in any
        # user-defined inventory file. Goes first so that hosts.yml
        # entries override it when present.
        ansible_args.extend(["-i", "%s," % host_spec])

        # Persistent user-level inventory in the config directory.
        # This survives Docker image pulls because the config dir
        # is mounted into the container.
        user_hosts = os.path.join(get_config_dir(), "hosts.yml")
        if os.path.isfile(user_hosts):
            ansible_args.extend(["-i", user_hosts])

        ansible_args.extend(["--limit", host_spec])

        # SSH user from user@host shorthand (hosts.yml ansible_user
        # still wins via inventory precedence).
        if ssh_user:
            ansible_args.extend(["-u", ssh_user])

        # Auto-accept new SSH host keys on first connection (matching
        # ssh's 'StrictHostKeyChecking=accept-new'). Known hosts with
        # changed keys are still rejected.
        os.environ.setdefault(
            "ANSIBLE_SSH_ARGS",
            "-o StrictHostKeyChecking=accept-new "
            "-o UserKnownHostsFile=~/.ssh/known_hosts",
        )

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
    skip_next = False
    # Known top-level commands
    cmd_names = {c["name"].split("_")[0] for c in data["commands"]}
    cmd_names |= {display_name(n) for n in cmd_names}
    # Global flags that consume the next token as a value
    global_value_flags = {"--host", "-H"}
    for arg in raw_args:
        if found_cmd:
            post_cmd.append(arg)
        elif skip_next:
            # This token is the value for --host/-H, not a command
            pre_cmd.append(arg)
            skip_next = False
        elif arg in global_value_flags:
            pre_cmd.append(arg)
            skip_next = True
        elif not arg.startswith("-") and arg in cmd_names:
            found_cmd = True
            post_cmd.append(arg)
        else:
            pre_cmd.append(arg)

    # Also accept --host/-H after the command (matching cobra's persistent
    # flag behavior in the Go CLI). Walk post_cmd, stopping at "--" so we
    # never touch passthrough args. Only consume the next token as a value
    # if it doesn't itself look like a flag.
    post_filtered = []
    i = 0
    while i < len(post_cmd):
        arg = post_cmd[i]
        if arg == "--":
            # Everything from here on is passthrough — leave it alone
            post_filtered.extend(post_cmd[i:])
            break
        # --host=value / -H=value form
        if arg.startswith("--host=") or arg.startswith("-H="):
            pre_cmd.append(arg)
            i += 1
            continue
        # --host value / -H value form
        if arg in global_value_flags:
            if i + 1 < len(post_cmd) and not post_cmd[i + 1].startswith("-"):
                pre_cmd.append(arg)
                pre_cmd.append(post_cmd[i + 1])
                i += 2
                continue
            # No valid value follows — leave it for argparse to error on
        post_filtered.append(arg)
        i += 1
    post_cmd = post_filtered

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
        cmd_name = resolve_command_name(args) if args.command else None
        # Derive positional param name from definitions
        cmd_index = {c["name"]: c for c in data["commands"]}
        cmd_def = cmd_index.get(cmd_name, {})
        pos_params = [p["name"] for p in cmd_def.get("parameters", [])
                      if p.get("positional")]
        if pos_params:
            setattr(args, pos_params[0], passthrough)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    command_name = resolve_command_name(args)
    if not command_name:
        parser.print_help()
        sys.exit(1)

    # Verify command exists in definitions
    all_cmd_names = {c["name"] for c in data["commands"]}
    if command_name not in all_cmd_names:
        print("Unknown command: %s" % command_name, file=sys.stderr)
        sys.exit(1)

    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(command_name, {})

    # Normalize orchestrator alias: k8s → kubernetes.
    if getattr(args, "orchestrator", None) == "k8s":
        args.orchestrator = "kubernetes"

    # Validate orchestrator-specific parameters.
    # If a parameter has orchestrator_only set, reject it when the user
    # selected a different orchestrator.
    orchestrator = getattr(args, "orchestrator", None)
    if orchestrator:
        for param in cmd_def.get("parameters", []):
            orch_only = param.get("orchestrator_only")
            if not orch_only:
                continue
            value = getattr(args, param["name"], None)
            if value is None or value == param.get("default"):
                continue
            if orchestrator != orch_only:
                print(
                    "Error: --%s can only be used with "
                    "--orchestrator %s"
                    % (param["name"].replace("_", "-"), orch_only),
                    file=sys.stderr,
                )
                sys.exit(1)

    # Interactive confirmation for destructive commands.
    # If the command defines a "yes" parameter and the user did not pass it,
    # prompt interactively rather than making them re-run with --yes.
    has_yes_param = any(
        p["name"] == "yes" for p in cmd_def.get("parameters", [])
    )
    if has_yes_param and not getattr(args, "yes", False):
        description = cmd_def.get("description", command_name)
        instance_id = getattr(args, "id", None) or "unknown"
        try:
            answer = input(
                "%s '%s'. Continue? [y/N] "
                % (description, instance_id)
            )
        except (EOFError, KeyboardInterrupt):
            print("\nOperation cancelled.")
            sys.exit(1)
        if answer.strip().lower() != "y":
            print("Operation cancelled.")
            sys.exit(0)
        # Tell the playbook to skip its own confirmation check
        args.yes = True

    ansible_playbook = find_ansible_playbook()
    ansible_args = build_ansible_args(
        ansible_playbook, command_name, args, data
    )

    os.execvp(ansible_args[0], ansible_args)


if __name__ == "__main__":
    main()
