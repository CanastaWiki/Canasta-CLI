#!/usr/bin/env python3
"""Canasta CLI wrapper -- translates CLI invocations into ansible-playbook calls.

Reads command definitions from meta/command_definitions.yml and builds
argparse subcommands with proper type-aware flag parsing. This replaces
the bash wrapper script with Cobra-equivalent argument handling.
"""

import argparse
import json
import os
import re
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
    "config": ["get", "set", "unset", "regenerate"],
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
    "storage": ["setup", "list"],
    "host": ["add", "remove", "list"],
    "argocd": ["ui", "password", "apps"],
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

# Named regex validators for parameters tagged with `validator: <name>`
# in meta/command_definitions.yml. Each entry is (compiled_regex,
# error_template). The error template is appended after the offending
# value, e.g. "Error: --domain-name 'foo' is not a valid hostname …".
#
# Validation runs after argparse but before any Ansible invocation, so
# bad values fail in milliseconds instead of after Argo CD/helm/image
# pull steps have already done work.
_VALIDATORS = {
    # RFC 1123 subdomain. Same regex k8s uses to validate
    # Ingress.spec.rules[].host. Lowercase letters/digits, '-' and '.'
    # only; each label starts and ends with an alphanumeric character.
    "hostname": (
        re.compile(
            r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?"
            r"(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
        ),
        "is not a valid hostname. Expected lowercase letters, digits, "
        "'-' and '.' only, with each label starting and ending with an "
        "alphanumeric character (e.g. 'example.com').",
    ),
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


_REMAINDER_HOIST_FLAGS = {
    "-w": "wiki", "--wiki": "wiki",
    "-i": "id", "--id": "id",
}


def hoist_flags_from_remainder(args):
    """Lift canasta subcommand flags out of a REMAINDER positional.

    Example: ``canasta maintenance script showJobs.php -w main`` — argparse
    sees ``showJobs.php`` as the first positional of ``script_args``
    (nargs=REMAINDER), so ``-w main`` gets eaten into the list instead of
    being recognized as the ``--wiki`` flag. Walk the list and lift any
    recognized flag (``-w``/``--wiki``, ``-i``/``--id``) plus its value out,
    setting the corresponding attribute on ``args`` if unset.

    Only applies to REMAINDER positionals (list values). Passthrough args
    supplied after ``--`` arrive as a pre-joined string and are not
    rewritten here — users who need a literal ``-w`` passed through to the
    inner script can use ``--`` explicitly.
    """
    for pos_name in ("script_args", "exec_args"):
        val = getattr(args, pos_name, None)
        if not isinstance(val, list):
            continue
        new_args = []
        i = 0
        while i < len(val):
            tok = val[i]
            dest = _REMAINDER_HOIST_FLAGS.get(tok)
            # Hoist only when the canasta flag isn't already set. If the user
            # typed the flag twice, preserve the second occurrence in the
            # positional list so it can be passed through to the inner script.
            if dest and i + 1 < len(val) and not getattr(args, dest, None):
                setattr(args, dest, val[i + 1])
                i += 2
                continue
            new_args.append(tok)
            i += 1
        setattr(args, pos_name, new_args)


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

        # Surface required_unless in --help output so the user sees the
        # conditional requirement before they run the command. argparse
        # has no native expression for "X is required unless Y is set",
        # so we encode the constraint in the description.
        ru = param.get("required_unless")
        if ru and "(required unless" not in desc:
            desc += " (required unless --%s is provided)" % ru.replace("_", "-")

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
            elif param.get("multi"):
                parser.add_argument(
                    name,
                    nargs="*",
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
        elif param.get("multi"):
            # Repeatable flag: --foo X --foo Y collects into a list.
            # Use action='append' so each occurrence appends to the
            # named dest, rather than the default which would store
            # only the last value (and Ansible-side filters that
            # expect a list would iterate over the string's chars).
            parser.add_argument(
                *flags,
                action="append",
                default=default if default is not None else [],
                required=required,
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


def resolve_instance(instance_id=None):
    """Resolve an instance from the registry by ID or working directory.

    Returns a dict with id, path, orchestrator keys, or exits on error.
    """
    conf_file = get_config_file_path()
    if not os.path.isfile(conf_file):
        print("Error: no registry found at %s" % conf_file, file=sys.stderr)
        sys.exit(1)
    with open(conf_file) as f:
        data = json.load(f)
    instances = data.get("Instances", {})

    if instance_id:
        if instance_id not in instances:
            print(
                "Error: instance '%s' not found in registry" % instance_id,
                file=sys.stderr,
            )
            sys.exit(1)
        inst = instances[instance_id]
        inst["id"] = instance_id
        return inst

    # Walk up from cwd to find a matching instance path
    search = os.path.abspath(os.getcwd())
    while True:
        for iid, inst in instances.items():
            if os.path.abspath(inst.get("path", "")) == search:
                inst["id"] = iid
                return inst
        parent = os.path.dirname(search)
        if parent == search:
            break
        search = parent

    print(
        "Error: no instance found for current directory", file=sys.stderr
    )
    sys.exit(1)


def handle_interactive_exec(args):
    """Handle maintenance exec by running docker/kubectl exec directly.

    Matches Go CLI behavior:
      - No -s, no command  -> list services (fall through to Ansible)
      - No -s, with command -> exec in web service
      - -s given, no command -> interactive /bin/bash in that service
      - -s given, with command -> exec in that service
    """
    service = getattr(args, "service", None) or ""
    exec_args = getattr(args, "exec_args", None) or []
    if isinstance(exec_args, list):
        command = exec_args
    else:
        command = exec_args.split() if exec_args else []

    # No service flag AND no command -> list services (let Ansible handle it)
    if not service and not command:
        return False

    inst = resolve_instance(getattr(args, "id", None))
    orchestrator = inst.get("orchestrator", "compose")

    if not service:
        service = "web"
    if not command:
        command = ["/bin/bash"]

    if orchestrator in ("kubernetes", "k8s"):
        import subprocess
        # Find the pod for this service
        ns = "canasta-%s" % inst["id"]
        result = subprocess.run(
            [
                "kubectl", "get", "pods", "-n", ns,
                "-l", "app.kubernetes.io/component=%s" % service,
                "-o", "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            print(
                "Error: no running pod found for service '%s'" % service,
                file=sys.stderr,
            )
            sys.exit(1)
        pod = result.stdout.strip()
        exec_args = ["kubectl", "exec", "-it", pod, "-n", ns, "--"]
        exec_args.extend(command)
        try:
            os.execvp("kubectl", exec_args)
        except FileNotFoundError:
            print("Error: kubectl not found on PATH", file=sys.stderr)
            sys.exit(1)
    else:
        host = inst.get("host", "localhost")
        docker_cmd = (
            ["docker", "compose", "exec", service] + command
        )
        if host and host != "localhost":
            # Run via SSH on the remote host
            import shlex
            remote_cmd = "cd %s && %s" % (
                shlex.quote(inst["path"]),
                " ".join(shlex.quote(a) for a in docker_cmd),
            )
            try:
                ssh_args = ["ssh", "-t", "-o", "LogLevel=ERROR"]
                # Include any custom SSH args (e.g. from ANSIBLE_SSH_ARGS)
                extra_ssh = os.environ.get("ANSIBLE_SSH_ARGS", "")
                if extra_ssh:
                    ssh_args.extend(extra_ssh.split())
                ssh_args.extend([host, remote_cmd])
                os.execvp("ssh", ssh_args)
            except FileNotFoundError:
                print("Error: ssh not found on PATH", file=sys.stderr)
                sys.exit(1)
        else:
            try:
                os.chdir(inst["path"])
            except FileNotFoundError:
                print(
                    "Error: instance path '%s' not found" % inst["path"],
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                os.execvp("docker", docker_cmd)
            except FileNotFoundError:
                print("Error: docker not found on PATH", file=sys.stderr)
                sys.exit(1)


class CanastaArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that augments the standard 'invalid choice' error
    with a 'Did you mean …?' suggestion when the user's input is close
    to a valid option.

    Argparse's stock error for an unknown subcommand is, e.g.:
        argument subcommand: invalid choice: 'up'
        (choose from ui, password, apps)
    Helpful but easy to miss. With this subclass:
        argument subcommand: invalid choice: 'up'
        (choose from ui, password, apps). Did you mean 'ui'?
    """

    # Match argparse's "invalid choice" message. The argument name
    # may be empty (e.g. when a parser uses metavar=""), so accept
    # zero-or-more chars before the colon. Capture the bad value and
    # the choices list.
    _INVALID_CHOICE_RE = re.compile(
        r"argument [^:]*: invalid choice: '([^']*)' \(choose from ([^)]+)\)"
    )

    def error(self, message):
        m = self._INVALID_CHOICE_RE.search(message)
        if m:
            import difflib
            bad = m.group(1)
            # Argparse emits the choice list comma-separated; some
            # Python versions quote individual choices, others don't.
            # Strip quotes after the split so both forms work.
            choices = [c.strip().strip("'\"") for c in m.group(2).split(",")]
            close = difflib.get_close_matches(bad, choices, n=1, cutoff=0.5)
            if close:
                message = message + " Did you mean '%s'?" % close[0]
        super().error(message)


def build_parser(data):
    """Build the full argparse parser from command definitions."""
    parser = CanastaArgumentParser(
        prog="canasta",
        description="Canasta MediaWiki management tool (Ansible edition).",
        epilog="Config file: %s" % get_config_file_path(),
    )

    # Global flags
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
        parser_class=CanastaArgumentParser,
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
            parser_class=CanastaArgumentParser,
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
                parser_class=CanastaArgumentParser,
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


def print_subcommand_help(group, data):
    """Print subcommands available for a group with one-line descriptions."""
    cmd_index = {c["name"]: c for c in data["commands"]}
    subcmds = SUBCOMMAND_GROUPS.get(group, [])
    nested = NESTED_SUBCOMMAND_GROUPS.get(group, {})

    rows = []
    for sub in subcmds:
        internal = "%s_%s" % (group, internal_name(sub))
        desc = cmd_index.get(internal, {}).get("description", "") or ""
        rows.append((sub, desc))
    # Include nested subcommand names (e.g., 'schedule' under 'backup')
    for nested_name in nested:
        if nested_name not in subcmds:
            # The nested group itself; describe it succinctly
            rows.append((nested_name, "(subcommand group; run 'canasta %s %s' for subcommands)" % (group, nested_name)))

    width = max((len(r[0]) for r in rows), default=0)
    print("Available '%s' subcommands:" % group)
    for name, desc in rows:
        print("  %-*s  %s" % (width, name, desc))
    print("")
    print("Run 'canasta %s <subcommand> --help' for details." % group)


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


def _cleanup_stale_vars_files():
    """Remove canasta-vars-*.json temp files older than 1 hour.

    These accumulate because os.execvp replaces the process before
    any cleanup can run. Each canasta invocation cleans up files left
    by previous invocations.
    """
    import glob
    import time
    cutoff = time.time() - 3600  # 1 hour
    for f in glob.glob(os.path.join(tempfile.gettempdir(), "canasta-vars-*.json")):
        try:
            if os.path.getmtime(f) < cutoff:
                os.unlink(f)
        except OSError:
            pass


def build_ansible_args(ansible_playbook, command_name, args, data):
    """Build the ansible-playbook command line.

    Extra vars are passed via a temp JSON file (-e @file) to avoid
    Jinja2 injection and shell quoting issues.
    """
    _cleanup_stale_vars_files()

    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(command_name, {})
    params = cmd_def.get("parameters", [])

    # Build extra vars as a dict, written to a JSON file
    extra_vars = {"command": command_name}

    # Pass target_host when the command declares --host. Commands
    # without a --host parameter resolve the target from the instance
    # registry via -i; argparse already rejects --host on those.
    host_value = getattr(args, "host", None)
    if host_value:
        extra_vars["target_host"] = host_value

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
        # Non-positional multi-valued flags (e.g. --public-ip) come
        # from argparse as a list and must reach Ansible as a list,
        # so playbook filters like `| join(', ')` operate over
        # elements rather than iterating a joined string char-by-char.
        if (
            isinstance(value, list)
            and param.get("multi")
            and not param.get("positional")
        ):
            if not value:
                continue
            extra_vars[name] = value
            continue
        # Positional multi (packages) and REMAINDER args (exec_args/
        # script_args) are consumed Ansible-side via .split(), so
        # collapse them to a space-joined string.
        if isinstance(value, list):
            value = " ".join(value)
            if not value:
                continue
        ptype = param.get("type", "string")
        if ptype == "bool":
            if value:
                extra_vars[name] = "true"
        elif ptype == "path":
            # path_kind: remote means the value is a path on the
            # target host (e.g. `create --path` is the new instance
            # dir on the remote). With --host set, we must NOT
            # rewrite it against the laptop's filesystem — that's how
            # `--path .` ended up as `/Users/<u>/...` on a Linux cp
            # host (#384).
            #
            # path_kind: local (the default) is for paths that name a
            # file on the laptop the user wants uploaded to the
            # remote (--envfile, --database, --global-settings, etc.)
            # or for the no-host case. Those still resolve against
            # the laptop cwd as before.
            path_kind = param.get("path_kind", "local")
            if host_value and path_kind == "remote":
                # Default "." (or empty) becomes the canonical
                # remote default. Ansible expands ~ on the target.
                if str(value) in (".", ""):
                    extra_vars[name] = "~/canasta"
                elif os.path.isabs(str(value)) or str(value).startswith("~"):
                    extra_vars[name] = str(value)
                else:
                    print(
                        "Error: --%s is relative ('%s') but --host is "
                        "set. With --host, --%s must be an absolute "
                        "path on the remote host (or a path starting "
                        "with '~'). Omit --%s to use the default "
                        "'~/canasta'."
                        % (
                            name.replace("_", "-"),
                            value,
                            name.replace("_", "-"),
                            name.replace("_", "-"),
                        ),
                        file=sys.stderr,
                    )
                    sys.exit(1)
            else:
                # Local resolution: expand ~ and resolve relatives
                # against the laptop cwd. Ansible otherwise resolves
                # relative paths against playbook_dir, which is the
                # canasta.py install directory — not what users
                # expect when they pass `-p .`.
                expanded = os.path.expanduser(str(value))
                if host_value and os.path.isabs(expanded):
                    extra_vars[name] = expanded
                else:
                    extra_vars[name] = os.path.abspath(expanded)
        else:
            extra_vars[name] = str(value)

    # Write vars to temp file (bypasses Jinja2 interpolation).
    # delete=False because ansible-playbook needs to read it after
    # execvp replaces this process. Stale files are cleaned up by
    # _cleanup_stale_vars_files() at the start of each invocation.
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

    if host_value:
        # Parse user@host shorthand.
        host_spec = host_value
        ssh_user = None
        if "@" in host_spec:
            ssh_user, host_spec = host_spec.split("@", 1)

        # Update target_host in the vars file to use just the hostname,
        # not the user@host form (the hosts.yml or inline inventory
        # entry is keyed on hostname alone).
        with open(vars_file.name, "r") as f:
            _v = json.load(f)
        _v["target_host"] = host_spec
        with open(vars_file.name, "w") as f:
            json.dump(_v, f)

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
    # ssh's 'StrictHostKeyChecking=accept-new'). Applies to all
    # commands, not just -H commands — when the host is resolved from
    # the registry, the SSH connection still needs this. Known hosts
    # with changed keys are still rejected.
    #
    # ForwardAgent=yes lets `canasta gitops` (and any other command
    # whose remote-side work shells out to ssh — e.g. `git push` to a
    # private repo) reuse the operator's local ssh-agent on the
    # target host. With no agent loaded the option is a no-op; with
    # one loaded, the user's keys flow through to the remote without
    # having to provision deploy keys on every gitops host.
    os.environ.setdefault(
        "ANSIBLE_SSH_ARGS",
        "-o StrictHostKeyChecking=accept-new "
        "-o UserKnownHostsFile=~/.ssh/known_hosts "
        "-o ForwardAgent=yes "
        # Long-running remote commands (helm upgrade --wait, gitops
        # init's git push, maintenance update) can outlast a NAT or
        # firewall idle timeout. ServerAliveInterval keeps the SSH
        # session warm so the parent doesn't see a "Broken pipe"
        # while the remote is still working.
        "-o ServerAliveInterval=30 -o ServerAliveCountMax=20",
    )

    # Hand the platform-correct config dir to Ansible. get_config_dir()
    # picks the macOS / Linux / root location; without exporting it,
    # YAML-side env lookups (e.g. roles/install/tasks/k3s_worker.yml)
    # fall through to a Linux-only fallback and miss hosts.yml on
    # macOS. setdefault preserves any pre-set value (canasta-docker
    # sets it explicitly, and tests use it for isolation).
    os.environ.setdefault("CANASTA_CONFIG_DIR", get_config_dir())

    return ansible_args


def main():
    data = load_definitions()
    parser = build_parser(data)

    # Extract --verbose from args BEFORE the subcommand only. Prevents
    # "-v" in "maintenance exec -i x php -v" from being consumed as
    # --verbose; a pre-command --verbose goes into a global parser, a
    # post-command --verbose (long form) is also hoisted, and -v after
    # a subcommand is left alone so passthrough scripts keep their -v.
    raw_args = sys.argv[1:]
    pre_cmd = []
    post_cmd = []
    found_cmd = False
    cmd_names = {c["name"].split("_")[0] for c in data["commands"]}
    cmd_names |= {display_name(n) for n in cmd_names}
    for arg in raw_args:
        if found_cmd:
            post_cmd.append(arg)
        elif not arg.startswith("-") and arg in cmd_names:
            found_cmd = True
            post_cmd.append(arg)
        else:
            pre_cmd.append(arg)

    # Hoist post-command --verbose into pre_cmd so the global parser
    # catches it. Stop at "--" so passthrough args are untouched. Only
    # the long form — "-v" after a subcommand is ambiguous (could be
    # "php -v") and stays where it is.
    post_filtered = []
    i = 0
    while i < len(post_cmd):
        arg = post_cmd[i]
        if arg == "--":
            post_filtered.extend(post_cmd[i:])
            break
        if arg == "--verbose":
            pre_cmd.append(arg)
            i += 1
            continue
        post_filtered.append(arg)
        i += 1
    post_cmd = post_filtered

    global_parser = argparse.ArgumentParser(add_help=False)
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

    # Merge global verbose into args
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

    hoist_flags_from_remainder(args)

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
        # Subcommand group invoked without a subcommand (e.g. 'canasta gitops'):
        # list the subcommands with descriptions instead of erroring.
        if command_name in SUBCOMMAND_GROUPS:
            print_subcommand_help(command_name, data)
            sys.exit(2)
        print("Unknown command: %s" % command_name, file=sys.stderr)
        sys.exit(1)

    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(command_name, {})

    # Normalize orchestrator alias: k8s → kubernetes.
    if getattr(args, "orchestrator", None) == "k8s":
        args.orchestrator = "kubernetes"

    # Validate required_unless parameters early, before spinning up
    # Ansible. Mirrors the check in roles/common/tasks/validate_params.yml
    # so the user sees the same message at parse time instead of waiting
    # for the playbook to load.
    for param in cmd_def.get("parameters", []):
        ru = param.get("required_unless")
        if not ru:
            continue
        own_value = getattr(args, param["name"], None)
        other_value = getattr(args, ru, None)
        if not own_value and not other_value:
            print(
                "Error: --%s is required unless --%s is provided"
                % (
                    param["name"].replace("_", "-"),
                    ru.replace("_", "-"),
                ),
                file=sys.stderr,
            )
            sys.exit(1)

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

    # Validate parameters tagged with `validator: <name>` against a
    # named regex. Catches typos (e.g. comma-vs-period in domain names)
    # before any Ansible work is done — much cheaper than the same
    # check failing at the end of the create pipeline.
    for param in cmd_def.get("parameters", []):
        validator_name = param.get("validator")
        if not validator_name:
            continue
        value = getattr(args, param["name"], None)
        if value is None or value == "":
            continue
        validator = _VALIDATORS.get(validator_name)
        if validator is None:
            # Unknown validator name in command_definitions.yml is a
            # bug in the YAML, not a user error — surface loudly.
            print(
                "Internal error: parameter '%s' references unknown "
                "validator '%s'" % (param["name"], validator_name),
                file=sys.stderr,
            )
            sys.exit(2)
        regex, error_template = validator
        if not regex.match(str(value)):
            print(
                "Error: --%s %r %s"
                % (
                    param["name"].replace("_", "-"),
                    str(value),
                    error_template,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

    # Interactive exec: bypass Ansible for TTY support.
    if command_name == "maintenance_exec":
        if handle_interactive_exec(args):
            # handle_interactive_exec calls os.execvp and never returns
            # when it handles the command; returns False to fall through
            # to Ansible for the service-listing case.
            pass

    # Direct command bypass: run simple commands without Ansible overhead.
    # direct_only commands always run via direct_commands and never fall
    # through to Ansible — CANASTA_FORCE_ANSIBLE has no effect on them
    # because they have no playbook.
    direct_only = cmd_def.get("direct_only", False)
    if direct_only or not os.environ.get("CANASTA_FORCE_ANSIBLE"):
        import direct_commands
        if direct_commands.is_direct_command(command_name):
            result = direct_commands.run_direct_command(command_name, args)
            if result is not direct_commands.FALLBACK:
                sys.exit(result)
            if direct_only:
                print(
                    "Error: command '%s' is direct_only but its handler "
                    "returned FALLBACK — no Ansible playbook to fall back to."
                    % command_name,
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
        instance_id = getattr(args, "id", None)
        host = getattr(args, "host", None)
        if instance_id:
            target = " '%s'" % instance_id
        elif host:
            target = " '%s'" % host
        else:
            target = ""
        try:
            answer = input(
                "%s%s. Continue? [y/N] "
                % (description, target)
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
