#!/usr/bin/env python3
"""Canasta CLI wrapper -- translates CLI invocations into ansible-playbook calls.

Reads command definitions from meta/command_definitions.yml and builds
argparse subcommands with proper type-aware flag parsing, replacing the
original bash wrapper script.
"""

import argparse
import datetime
import fcntl
import json
import os
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

import yaml


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFINITIONS_PATH = os.path.join(SCRIPT_DIR, "meta", "command_definitions.yml")
CANASTA_YML = os.path.join(SCRIPT_DIR, "canasta.yml")
ANSIBLE_CFG = os.path.join(SCRIPT_DIR, "ansible.cfg")

# Exit codes a wrapping script can branch on. 0 success; 1 unexpected
# error; 2 usage error (argparse). 3 flags an expected precondition
# refusal — the target already exists — so CI can special-case it
# instead of treating it like a crash. 4 means the instance is busy.
EXIT_ALREADY_EXISTS = 3
# Another command that changes the same instance is still running.
EXIT_INSTANCE_BUSY = 4

# Registry-location and lookup helpers are shared with the
# canasta_registry Ansible module (which reads/writes conf.json), so the
# CLI and the module never disagree about where the registry lives. The
# helpers live under the role's module_utils dir; append it to the path
# so `import canasta_config` resolves the same file Ansible ships.
sys.path.append(os.path.join(SCRIPT_DIR, "roles", "common", "module_utils"))
import canasta_config
from direct_commands._helpers import (
    forward_agent_option,
    _compose_profile_args,
    _is_k8s,
    _is_local_target,
    _read_env,
    _read_env_file,
    _read_registry,
    _resolve_compose_cmd,
    resolve_instance,
)
from canasta_self_update import (
    _install_group_shared,
    _version_major,
    self_update_cli,
)
import canasta_image_pin
from canasta_docker_mode import (
    check_docker_mode_can_reach_runtime,
    check_docker_mode_install_target,
    check_docker_mode_key_path,
)

# Ensure Ansible uses the repo's config regardless of working directory
os.environ.setdefault("ANSIBLE_CONFIG", ANSIBLE_CFG)


def _ca_bundle_override(environ, verify_paths, path_exists, certifi_where):
    """Decide whether to point SSL_CERT_FILE at certifi's CA bundle.

    Returns the certifi bundle path when the interpreter has no usable
    system CA store, or None to leave the environment untouched.

    python.org's macOS Python ships without a working CA store, so the
    venv's `ansible-galaxy` fails to verify TLS to galaxy.ansible.com with
    CERTIFICATE_VERIFY_FAILED. We fall back to certifi, but only when:
      * the user hasn't set SSL_CERT_FILE themselves (corporate CA / custom
        trust store wins), and
      * the interpreter has no usable system CA store — so Linux and
        Homebrew Python, which verify against the OS trust store, are left
        alone (avoids overriding a system store that trusts internal CAs).

    `certifi_where` is passed in as a callable so the policy stays pure and
    import-free for unit tests.
    """
    if environ.get("SSL_CERT_FILE"):
        return None
    cafile = verify_paths.cafile
    capath = verify_paths.capath
    if (cafile and path_exists(cafile)) or (capath and path_exists(capath)):
        return None
    return certifi_where()


def _ensure_ca_bundle():
    """Export SSL_CERT_FILE/REQUESTS_CA_BUNDLE for interpreters lacking a
    system CA store (see _ca_bundle_override). No-op on Linux / when the
    user configured their own bundle, and silently skipped if certifi isn't
    installed yet (e.g. before the first dependency refresh)."""
    try:
        import ssl
        import certifi
    except ImportError:
        return
    override = _ca_bundle_override(
        os.environ, ssl.get_default_verify_paths(), os.path.exists, certifi.where
    )
    if override:
        os.environ["SSL_CERT_FILE"] = override
        os.environ.setdefault("REQUESTS_CA_BUNDLE", override)


_ensure_ca_bundle()

# Commands that have subcommands (e.g., "config get" -> "config_get")
SUBCOMMAND_GROUPS = {
    "config": ["get", "set", "unset", "refresh-template"],
    "extension": ["list", "enable", "disable", "set-version", "add", "remove"],
    "skin": ["list", "enable", "disable", "add", "remove"],
    "maintenance": ["update", "script", "extension", "exec"],
    "crowdsec": [
        "bouncer-enroll", "console-enroll", "reload",
        "status", "scenarios", "alerts", "metrics",
        "ban", "unban",
    ],
    "devmode": ["enable", "disable"],
    "sitemap": ["generate", "remove"],
    "backup": [
        "init", "list", "create", "restore", "delete",
        "unlock", "files", "check", "diff", "purge",
    ],
    "gitops": [
        "init", "join", "add", "rm", "push", "pull", "resolve",
        "status", "diff", "fix-submodules", "set-role",
        "remove-host",
    ],
    "host": ["add", "remove", "list"],
    "sidecar": ["add", "list", "remove", "migrate"],
    "schedule": ["add", "list", "remove", "run"],
    "image": ["prune", "build-args", "set-build-arg", "unset-build-arg"],
}

# Nested subcommand groups (backup schedule set|list|remove)
NESTED_SUBCOMMAND_GROUPS = {
    "backup": {
        "schedule": ["set", "list", "remove"],
        "databases": ["add", "remove", "list"],
    },
}


def _hostname_hint(value):
    """Extra guidance when a rejected hostname carries a scheme or a port —
    the most common reason --domain-name validation fails (users expect to
    pass a URL or host:port). Ports/schemes are configured elsewhere."""
    text = str(value)
    if "://" in text or re.search(r":[0-9]+$", text):
        return (
            " Do not include a scheme or port here: pass --domain-name as a "
            "bare hostname (e.g. 'example.com') and set HTTP_PORT/HTTPS_PORT "
            "(and TLS_MODE=off for plain HTTP) in your .env file."
        )
    return ""


# Named regex validators for parameters tagged with `validator: <name>`
# in meta/command_definitions.yml. Each entry is
# (compiled_regex, error_template[, hint_fn]). The error template is
# appended after the offending value, e.g.
# "Error: --domain-name 'foo' is not a valid hostname …". The optional
# hint_fn(value) returns extra, value-specific guidance to append.
#
# Validation runs after argparse but before any Ansible invocation, so
# bad values fail in milliseconds instead of after image pulls and other
# steps have already done work.
_VALIDATORS = {
    # RFC 1123 subdomain: lowercase letters/digits, '-' and '.' only;
    # each label starts and ends with an alphanumeric character.
    "hostname": (
        re.compile(
            r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?"
            r"(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
        ),
        "is not a valid hostname. Expected lowercase letters, digits, "
        "'-' and '.' only, with each label starting and ending with an "
        "alphanumeric character (e.g. 'example.com').",
        _hostname_hint,
    ),
    # restic reads a comma as a separator between tags.
    "backup_tag": (
        re.compile(r"^[A-Za-z0-9_.:-]+$"),
        "is not a valid backup tag. Expected letters, digits, '_', '.', "
        "':' and '-' only (e.g. 'before-upgrade').",
    ),
}


# Parameter validation runs at two layers by design. These functions are
# the CLI's fast-fail half: they run after argparse but before any Ansible
# work, so a typo (comma-vs-period domain, a missing required_unless
# partner) fails in milliseconds instead of after image pulls and other
# steps have already done work. The playbook re-validates the same
# command_definitions.yml metadata in roles/common/tasks/validate_params.yml
# so values that bypass the CLI (CANASTA_FORCE_ANSIBLE, a raw
# ansible-playbook run) are still caught. Both layers read the same
# definition fields; neither is authoritative alone. Each function returns
# a list of (exit_code, message) so the caller decides how to surface them.


def _validate_required_unless(cmd_def, get_value):
    """Flag params whose required_unless partner is also unset."""
    errors = []
    for param in cmd_def.get("parameters", []):
        ru = param.get("required_unless")
        if not ru:
            continue
        if not get_value(param["name"]) and not get_value(ru):
            errors.append((
                1,
                "Error: --%s is required unless --%s is provided"
                % (param["name"].replace("_", "-"), ru.replace("_", "-")),
            ))
    return errors


def _validate_mutual_exclusion(cmd_def, get_value):
    """Flag params set together with their declared mutual_exclusion partner.

    Both partners typically declare the relationship, so the conflicting
    pair is reported once (keyed on the sorted name pair).
    """
    errors = []
    params_by_name = {p["name"]: p for p in cmd_def.get("parameters", [])}

    def _is_set(param):
        if param is None:
            return False
        value = get_value(param["name"])
        if value is None or value == "":
            return False
        # A param left at its default (e.g. a bool flag not passed) is
        # not "provided" and so does not conflict.
        return value != param.get("default")

    seen = set()
    for param in cmd_def.get("parameters", []):
        partner_name = param.get("mutual_exclusion")
        if not partner_name:
            continue
        pair = tuple(sorted((param["name"], partner_name)))
        if pair in seen:
            continue
        if _is_set(param) and _is_set(params_by_name.get(partner_name)):
            seen.add(pair)
            errors.append((
                1,
                "Error: --%s cannot be combined with --%s"
                % (pair[0].replace("_", "-"), pair[1].replace("_", "-")),
            ))
    return errors


def _validate_named_validators(cmd_def, get_value, validators=None):
    """Check params tagged `validator: <name>` against the named regex.

    An unknown validator name is a bug in command_definitions.yml, not a
    user error, so it surfaces as an internal error (exit 2).
    """
    if validators is None:
        validators = _VALIDATORS
    errors = []
    for param in cmd_def.get("parameters", []):
        validator_name = param.get("validator")
        if not validator_name:
            continue
        value = get_value(param["name"])
        if value is None or value == "":
            continue
        validator = validators.get(validator_name)
        if validator is None:
            errors.append((
                2,
                "Internal error: parameter '%s' references unknown "
                "validator '%s'" % (param["name"], validator_name),
            ))
            continue
        regex, error_template = validator[0], validator[1]
        hint_fn = validator[2] if len(validator) > 2 else None
        if not regex.match(str(value)):
            hint = hint_fn(value) if hint_fn else ""
            errors.append((
                1,
                "Error: --%s %r %s%s"
                % (
                    param["name"].replace("_", "-"),
                    str(value),
                    error_template,
                    hint,
                ),
            ))
    return errors


def collect_cli_param_errors(cmd_def, args):
    """Run the CLI-layer parameter validations and return a list of
    (exit_code, message) tuples, in evaluation order. Empty when valid.

    `args` is the parsed argparse Namespace.
    """
    def get_value(name):
        return getattr(args, name, None)

    return (
        _validate_required_unless(cmd_def, get_value)
        + _validate_mutual_exclusion(cmd_def, get_value)
        + _validate_named_validators(cmd_def, get_value)
    )


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
    recognized flag (``-w``/``--wiki``, ``-i``/``--id``, or the single-token
    ``--wiki=X``/``--id=X``) plus its value out, setting the corresponding
    attribute on ``args`` if unset.

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
            flag, eq, inline = tok.partition("=")
            if eq and flag.startswith("--"):
                dest = _REMAINDER_HOIST_FLAGS.get(flag)
                if (dest and hasattr(args, dest)
                        and not getattr(args, dest, None)):
                    setattr(args, dest, inline)
                    i += 1
                    continue
                new_args.append(tok)
                i += 1
                continue
            dest = _REMAINDER_HOIST_FLAGS.get(tok)
            # Hoist only when the canasta flag isn't already set. If the user
            # typed the flag twice, preserve the second occurrence in the
            # positional list so it can be passed through to the inner script.
            # A flag the command does not define (maintenance exec has no
            # --wiki) belongs to the inner command and stays in the list.
            if (dest and hasattr(args, dest) and i + 1 < len(val)
                    and not getattr(args, dest, None)):
                setattr(args, dest, val[i + 1])
                i += 2
                continue
            new_args.append(tok)
            i += 1
        setattr(args, pos_name, new_args)


class _DynamicChoices:
    """argparse choices that accept a fixed list plus a prefix pattern.

    Used for `canasta install` where the base packages are fixed
    (docker, podman, etc.) but `uv:<tool>` is an open-ended prefix
    (e.g. `uv:podman-compose`).  argparse calls `__contains__` for
    validation and `__iter__` for --help output.

    `pattern`, when given, constrains the tail after the prefix. It
    mirrors the playbook's own validation (install.yml rejects a
    `uv:` target whose tail has other characters), so argparse fails
    fast on the same values instead of letting them through to a
    post-SSH playbook error.
    """

    def __init__(self, base, prefix, pattern=None):
        self._base = list(base)
        self._prefix = prefix
        self._pattern = re.compile(pattern) if pattern else None

    def __contains__(self, item):
        if item in self._base:
            return True
        if not isinstance(item, str) or not item.startswith(self._prefix):
            return False
        tail = item[len(self._prefix):]
        if not tail:
            return False
        if self._pattern:
            return bool(self._pattern.fullmatch(tail))
        return True

    def __iter__(self):
        return iter(self._base)


def _positional_choices(param, name):
    """argparse kwargs constraining a positional to a fixed set of values.

    A positional with `choices` deliberately drops `metavar`: argparse
    prints the choice set in the usage line only when no metavar shadows
    it, and advertising the valid values is the point. Without choices,
    the uppercase metavar is kept.
    """
    choices = param.get("choices")
    if choices:
        prefix = param.get("choices_dynamic_prefix")
        if prefix:
            return {"choices": _DynamicChoices(
                choices, prefix, param.get("choices_dynamic_pattern"))}
        return {"choices": choices}
    return {"metavar": name.upper()}


def is_required_flag(param):
    """True for a non-positional param the user must (or usually must) pass."""
    if param.get("positional"):
        return False
    return bool(param.get("required") or param.get("required_unless"))


def add_params_to_parser(parser, params):
    """Add command parameters to an argparse parser based on definitions.

    Required flags are listed first, under a "required arguments"
    heading shown above the "options" heading.
    """
    required_group = None
    if any(is_required_flag(p) for p in params):
        required_group = parser.add_argument_group("required arguments")
        # argparse prints groups in creation order; move this one from
        # the end to just after "positional arguments".
        parser._action_groups.insert(1, parser._action_groups.pop())

    for param in sorted(params, key=lambda p: not is_required_flag(p)):
        target = required_group if is_required_flag(param) else parser
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
                target.add_argument(
                    name,
                    nargs=argparse.REMAINDER,
                    default=default,
                    help=desc,
                    metavar=name.upper(),
                )
            elif param.get("multi"):
                target.add_argument(
                    name,
                    # `required: true` on a positional multi means at least
                    # one value must be given (argparse errors on none).
                    nargs="+" if required else "*",
                    default=default,
                    help=desc,
                    **_positional_choices(param, name)
                )
            else:
                target.add_argument(
                    name,
                    nargs="?",
                    default=default,
                    help=desc,
                    **_positional_choices(param, name)
                )
        elif ptype == "bool":
            target.add_argument(
                *flags,
                action="store_true",
                default=False,
                help=desc,
                dest=name,
            )
            # Deprecated spellings keep working but stay out of --help.
            if param.get("aliases"):
                target.add_argument(
                    *param["aliases"],
                    action="store_true",
                    default=False,
                    help=argparse.SUPPRESS,
                    dest=name,
                )
        elif ptype == "choice":
            choices = param.get("choices", [])
            target.add_argument(
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
            target.add_argument(
                *flags,
                action="append",
                default=default if default is not None else [],
                required=required,
                help=desc,
                dest=name,
            )
        else:
            target.add_argument(
                *flags,
                default=default,
                required=required,
                help=desc,
                dest=name,
            )


def get_config_dir():
    """Return the user config directory (where conf.json lives).

    Delegates to the shared helper so the CLI resolves the same location
    the canasta_registry module writes to.
    """
    return canasta_config.get_config_dir()


def get_config_file_path():
    """Return the path to conf.json."""
    return canasta_config.config_path(get_config_dir())


# Commands that change an instance wholesale or start and stop it. Each holds
# an exclusive per-instance lock for its whole run, so two of them never
# interleave on one instance.
LOCKED_COMMANDS = {
    "backup_restore": "restore",
    "delete": "delete",
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "rebuild": "rebuild",
    "image_set_build_arg": "rebuild",
    "image_unset_build_arg": "rebuild",
}


def _user_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return "uid %d" % uid


def check_root_in_foreign_config_dir():
    """Refuse to run as root in a config directory another user owns, before
    anything in it is written: what root creates there, such as instance
    locks and the HAProxy edge's files, its owner could not change."""
    if os.getuid() != 0:
        return
    config_dir = get_config_dir()
    try:
        uid = os.stat(config_dir).st_uid
    except OSError:
        return
    if uid != 0:
        owner = _user_name(uid)
        print(
            "Error: canasta is running as root, but its config directory %s "
            "belongs to %s. Run canasta as %s: as root it would write files "
            "%s could not change." % (config_dir, owner, owner, owner),
            file=sys.stderr,
        )
        sys.exit(1)


def warn_root_on_shared_install():
    """Warn once per run when root runs the native CLI from the install
    shared by group canasta."""
    if (os.geteuid() != 0
            or os.environ.get("CANASTA_RUN_MODE") == "docker"
            # The re-exec after a self-update has already warned.
            or os.environ.get("CANASTA_SELF_UPDATED")
            or not _install_group_shared(SCRIPT_DIR)):
        return
    print(
        "Warning: canasta is running as root. The CLI install is meant to be "
        "run as a member of the canasta group; files root creates here can "
        "block later updates for that user.",
        file=sys.stderr,
    )


KUBERNETES_REFUSAL = (
    "Error: Canasta CLI 5.x runs MediaWiki 1.47 on Docker Compose only, so "
    "it does not set up or manage Kubernetes. Use the 4.x CLI for Kubernetes "
    "instances, or move them to Docker Compose with a backup and a restore."
)

# Commands that act on every registered instance.
ACROSS_INSTANCES_COMMANDS = {"upgrade"}


def uses_kubernetes(command_name, cmd_def, args):
    """Whether this run acts on a Kubernetes instance a 4.x CLI created."""
    if any(p.get("name") == "id" for p in cmd_def.get("parameters", [])):
        inst = resolve_instance(getattr(args, "id", None), required=False)
        return bool(inst) and _is_k8s(inst)
    if command_name not in ACROSS_INSTANCES_COMMANDS:
        return False
    if not os.path.isfile(get_config_file_path()):
        return False
    instances = canasta_config.read_config(get_config_dir()).get(
        "Instances", {})
    return any(_is_k8s(i) for i in instances.values())


def refuse_kubernetes(command_name, cmd_def, args):
    if uses_kubernetes(command_name, cmd_def, args):
        print(KUBERNETES_REFUSAL, file=sys.stderr)
        sys.exit(1)


def acquire_instance_lock(command_name, args):
    """Hold the instance's command lock for the rest of this run.

    The lock is an flock on <config dir>/locks/<id>.lock, a directory the
    Dockerized CLI shares through its config mount. The descriptor stays
    open and inheritable, so it passes through the exec into
    ansible-playbook and the kernel releases it when the run ends, however
    it ends. An unresolvable instance is left to the command to report.
    """
    action = LOCKED_COMMANDS.get(command_name)
    if action is None:
        return None
    inst = resolve_instance(getattr(args, "id", None), required=False)
    if not inst:
        return None
    lock_dir = os.path.join(get_config_dir(), "locks")
    lock_path = os.path.join(lock_dir, "%s.lock" % inst["id"])
    try:
        os.makedirs(lock_dir, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except PermissionError:
        user = _user_name(os.getuid())
        print(
            "Error: %s cannot open the instance lock %s. Make %s its owner "
            "again (for example, sudo chown %s %s) and try again."
            % (user, lock_path, user, user, lock_path),
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            holder = json.loads(os.read(fd, 4096) or b"{}")
        except ValueError:
            holder = {}
        os.close(fd)
        print(
            "Error: a %s of instance '%s' is in progress (started %s on %s). "
            "Wait for it to finish, then try again."
            % (holder.get("command", "command"), inst["id"],
               holder.get("started", "at an unknown time"),
               holder.get("host", "this controller")),
            file=sys.stderr,
        )
        sys.exit(EXIT_INSTANCE_BUSY)
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps({
        "command": action,
        "started": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": os.environ.get("CANASTA_HOST_NAME") or socket.gethostname(),
    }).encode())
    os.set_inheritable(fd, True)
    return fd


def check_create_precondition(args):
    """For `create`, refuse a duplicate instance ID before Ansible runs.

    Mirrors the `_validate_inputs.yml` registry check so an existing ID
    fails in milliseconds with exit code 3 (EXIT_ALREADY_EXISTS) — a
    distinct, expected refusal a wrapping script can branch on, rather
    than the generic Ansible task-failure code. The playbook's own
    `fail:` stays as the backstop. Returns without acting when the
    registry is absent or the ID is free.
    """
    instance_id = getattr(args, "id", None)
    if not instance_id:
        return
    conf_file = get_config_file_path()
    if not os.path.isfile(conf_file):
        return
    instances = canasta_config.read_config(get_config_dir()).get("Instances", {})
    existing = instances.get(instance_id)
    if existing is None:
        return
    print(
        "Error: Instance '%s' already exists in the registry "
        "(path: %s, host: %s). "
        "Run 'canasta delete --id %s' to remove it first."
        % (
            instance_id,
            existing.get("path", "unknown"),
            existing.get("host", "localhost"),
            instance_id,
        ),
        file=sys.stderr,
    )
    sys.exit(EXIT_ALREADY_EXISTS)


def _redirect_stdin_from_file(path):
    """Point fd 0 at `path` so the about-to-be-exec'd command reads it as
    stdin. Used by `maintenance exec --stdin-file`. No-op when path is unset.

    This runs in the direct-exec path (handle_interactive_exec os.execvp's
    docker/ssh, replacing this process), so there is no shell to do
    a `< file` redirect — we dup the file onto stdin ourselves.
    """
    if not path:
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        print(
            "Error: cannot read --stdin-file '%s': %s" % (path, exc),
            file=sys.stderr,
        )
        sys.exit(1)
    os.dup2(fd, 0)
    os.close(fd)


def handle_interactive_exec(args):
    """Handle maintenance exec by running docker compose exec directly.

    Dispatch rules:
      - No -s, no command  -> list services (fall through to Ansible)
      - No -s, with command -> exec in web service
      - -s given, no command -> interactive /bin/bash in that service
      - -s given, with command -> exec in that service
    """
    import shlex
    # The local Compose branch chdirs into the instance before the file is
    # opened, so a relative --stdin-file must be resolved against the
    # operator's directory now.
    if getattr(args, "stdin_file", None):
        args.stdin_file = os.path.abspath(args.stdin_file)
    service = getattr(args, "service", None) or ""
    exec_args = getattr(args, "exec_args", None) or []
    if isinstance(exec_args, list):
        command = exec_args
    else:
        # exec_args arrives as a string when main() collected it from a `--`
        # passthrough. Split it like a shell would so quoted arguments (a
        # --summary with spaces, a page title with spaces) survive as single
        # argv elements — a naive .split() would shred them on whitespace.
        command = shlex.split(exec_args) if exec_args else []

    # No service flag AND no command -> list services (let Ansible handle it)
    if not service and not command:
        return False

    inst = resolve_instance(getattr(args, "id", None))

    if not service:
        service = "web"
    if not command:
        # Interactive shell: prefer bash, but fall back to sh so a slim sidecar
        # image (alpine, *-slim) without bash still opens a usable shell. A
        # command (the else branch above) is exec'd directly and needs no shell.
        command = ["/bin/sh", "-c",
                   "if command -v bash >/dev/null 2>&1; then exec bash; "
                   "else exec sh; fi"]

    host = inst.get("host", "localhost")
    # With --stdin-file the exec must be non-interactive so the piped
    # payload reaches the command: `docker compose exec -T` (no TTY).
    # Without it, omit -T to preserve the interactive shell behavior.
    stdin_file = getattr(args, "stdin_file", None)
    docker_cmd = (_resolve_compose_cmd(inst)
                  + _compose_profile_args(inst) + ["exec"])
    if stdin_file:
        docker_cmd.append("-T")
    docker_cmd += [service] + command
    docker_host = inst.get("dockerHost")
    if host and host != "localhost":
        # Run via SSH on the remote host. ssh forwards our stdin to the
        # remote command, so the --stdin-file payload (dup'd onto fd 0
        # below) flows through to `docker compose exec -T`. Use -T (no
        # remote TTY) when piping; -t for interactive sessions.
        import shlex
        remote_cmd = "cd %s && %s" % (
            shlex.quote(inst["path"]),
            " ".join(shlex.quote(a) for a in docker_cmd),
        )
        if docker_host:
            remote_cmd = "export DOCKER_HOST=%s; %s" % (
                shlex.quote(docker_host), remote_cmd)
        try:
            ssh_args = ["ssh", "-T" if stdin_file else "-t",
                        "-o", "LogLevel=ERROR"]
            # Include any custom SSH args (e.g. from ANSIBLE_SSH_ARGS)
            extra_ssh = os.environ.get("ANSIBLE_SSH_ARGS", "")
            if extra_ssh:
                ssh_args.extend(extra_ssh.split())
            ssh_args.extend([host, remote_cmd])
            _redirect_stdin_from_file(stdin_file)
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
        if docker_host:
            os.environ["DOCKER_HOST"] = docker_host
        try:
            _redirect_stdin_from_file(stdin_file)
            os.execvp(docker_cmd[0], docker_cmd)
        except FileNotFoundError:
            print("Error: %s not found on PATH" % docker_cmd[0],
                  file=sys.stderr)
            sys.exit(1)


class CanastaArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that augments the standard 'invalid choice' error
    with a 'Did you mean …?' suggestion when the user's input is close
    to a valid option.

    Argparse's stock error for an unknown subcommand is, e.g.:
        argument subcommand: invalid choice: 'lst'
        (choose from add, list, remove)
    Helpful but easy to miss. With this subclass:
        argument subcommand: invalid choice: 'lst'
        (choose from add, list, remove). Did you mean 'list'?
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


def nested_group_for(command_name):
    """If command_name names a bare nested group (e.g. 'backup_schedule' or
    'backup_databases'), return (group, nested_group); otherwise None.

    Lets the dispatcher treat a nested group invoked without a leaf
    subcommand the same way it treats a bare top-level group — by listing
    its subcommands rather than erroring with "Unknown command".
    """
    for group, nested_map in NESTED_SUBCOMMAND_GROUPS.items():
        for nested_group in nested_map:
            if command_name == "%s_%s" % (group, internal_name(nested_group)):
                return (group, nested_group)
    return None


def print_nested_subcommand_help(group, nested_group, data):
    """Print the subcommands of a nested group (e.g. 'backup schedule')."""
    cmd_index = {c["name"]: c for c in data["commands"]}
    subcmds = NESTED_SUBCOMMAND_GROUPS.get(group, {}).get(nested_group, [])
    rows = []
    for sub in subcmds:
        internal = "%s_%s_%s" % (
            group, internal_name(nested_group), internal_name(sub)
        )
        desc = cmd_index.get(internal, {}).get("description", "") or ""
        rows.append((sub, desc))

    width = max((len(r[0]) for r in rows), default=0)
    print("Available '%s %s' subcommands:" % (group, nested_group))
    for name, desc in rows:
        print("  %-*s  %s" % (width, name, desc))
    print("")
    print(
        "Run 'canasta %s %s <subcommand> --help' for details."
        % (group, nested_group)
    )


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

    canasta.yml removes its own vars file, but a run that fails before
    its first task (or never reaches execvp) leaves one behind. Each
    canasta invocation cleans up files left by previous invocations.
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


def ssh_control_path_dir(base="/tmp"):
    """Return a directory for SSH control sockets, or None.

    Per user, because Ansible refuses a control directory it cannot
    write to, and private, because whoever can replace a socket in it
    can stand in for the SSH connection. Kept under /tmp because the
    socket path must fit in sun_path.
    """
    path = os.path.join(base, ".canasta-ssh-%d" % os.getuid())
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.lstat(path)
    except OSError:
        return None
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & 0o077):
        return None
    return path


def instance_host_for_paths(cmd_def, args):
    """Return the host whose filesystem a path_kind: instance_host value names.

    A command that declares --host targets that host (or this machine);
    any other command acts on a registered instance, whose host is in the
    registry on this machine.
    """
    if any(p["name"] == "host" for p in cmd_def.get("parameters", [])):
        return getattr(args, "host", None) or "localhost"
    if not os.path.isfile(get_config_file_path()):
        return "localhost"
    inst = resolve_instance(getattr(args, "id", None), required=False)
    return (inst or {}).get("host") or "localhost"


def resolve_instance_host_path(name, value, host):
    """Resolve a path on the instance's host, or exit if it cannot be.

    On this machine the value is expanded and anchored to the working
    directory. On another host it is passed through for that host to
    expand, so it must not depend on a working directory there.
    """
    value = str(value)
    if _is_local_target(host):
        return os.path.abspath(os.path.expanduser(value))
    if os.path.isabs(value) or value.startswith("~"):
        return value
    print(
        "Error: --%s is relative ('%s'), but the instance is on %s. "
        "Give an absolute path on %s, or one starting with a quoted '~' "
        "(for example '~/x')."
        % (name.replace("_", "-"), value, host, host),
        file=sys.stderr,
    )
    sys.exit(1)


def apply_instance_host_paths(cmd_def, args):
    """Resolve every path_kind: instance_host parameter set on args."""
    host = None
    for param in cmd_def.get("parameters", []):
        if param.get("path_kind") != "instance_host":
            continue
        value = getattr(args, param["name"], None)
        if value is None:
            continue
        if host is None:
            host = instance_host_for_paths(cmd_def, args)
        setattr(args, param["name"],
                resolve_instance_host_path(param["name"], value, host))


def _add_param_extra_vars(params, args, host_value, extra_vars):
    """Add each command parameter the user set to `extra_vars`, in the
    form the playbooks read it."""
    # Command parameters
    for param in params:
        name = param["name"]
        value = getattr(args, name, None)
        if value is None:
            continue
        # Non-positional multi-valued flags (e.g. --public-ip) and
        # as_list positionals (config set's KEY=VALUE settings, whose
        # values may contain spaces) come from argparse as a list and
        # must reach Ansible as a list,
        # so playbook filters like `| join(', ')` operate over
        # elements rather than iterating a joined string char-by-char.
        if (
            isinstance(value, list)
            and param.get("multi")
            and (param.get("as_list") or not param.get("positional"))
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
            # host.
            #
            # path_kind: local (the default) is for paths that name a
            # file on the laptop the user wants uploaded to the
            # remote (--envfile, --database, --global-settings, etc.)
            # or for the no-host case. Those still resolve against
            # the laptop cwd as before.
            path_kind = param.get("path_kind", "local")
            if path_kind == "instance_host":
                # Already resolved by apply_instance_host_paths.
                extra_vars[name] = str(value)
            elif host_value and path_kind == "remote":
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


def _add_runtime_extra_vars(args, host_value, extra_vars):
    """Add compose_command / inspect_command for the targeted instance
    when the registry or its .env records them."""
    # Runtime resolution: a registry entry (composeCommand /
    # inspectCommand) wins, then the instance's .env for instances
    # created before those fields existed.
    #
    # Nothing else is injected. An extra-var outranks every other
    # source, including the set_fact that create_preflight.yml's
    # runtime probe uses, so guessing a runtime here would silently
    # override a probe that actually asked the target what it runs.
    # Detection belongs to that probe; the play-scope default in
    # vars/compose_runtime.yml covers everything in between.
    if "compose_command" not in extra_vars:
        try:
            # required=False: this lookup is opportunistic — plenty of
            # commands legitimately run with no resolvable instance — so
            # it must not print an error the caller then swallows.
            inst = resolve_instance(
                getattr(args, "id", None), required=False
            ) if os.path.isfile(get_config_file_path()) else None
            inst = inst or {}
            # The registry is on the controller, so it reads the same
            # whatever host the instance runs on — unlike the .env below,
            # and it is where create records the runtime it probed.
            for _key, _field in (("compose_command", "composeCommand"),
                                 ("inspect_command", "inspectCommand")):
                reg_val = inst.get(_field)
                if reg_val:
                    extra_vars[_key] = reg_val
            path = inst.get("path", "")
            # The .env sits on the instance's host, so it is only
            # readable from here when that host is this machine. Kept as
            # a fallback for instances created before the registry
            # carried the runtime fields.
            is_local = (inst.get("host") or "localhost") in ("localhost", "")
            if path and is_local and not host_value:
                for _key in ("compose_command", "inspect_command"):
                    if _key in extra_vars:
                        continue
                    env_val = _read_env(path, _key)
                    if env_val:
                        extra_vars[_key] = env_val
        except (OSError, IOError, KeyError):
            pass

    # Infer compose_command from inspect_command or dockerHost. An
    # instance whose inspectCommand in the registry is 'podman' but
    # whose composeCommand was never recorded (e.g. from a state=update
    # that set only the former) would otherwise fall back to 'docker
    # compose'. A legacy instance that predates the runtime fields
    # entirely carries only the podman socket in dockerHost.
    if "compose_command" not in extra_vars:
        if extra_vars.get("inspect_command") == "podman":
            extra_vars["compose_command"] = "podman-compose"
        elif "compose_command" not in extra_vars:
            try:
                inst = resolve_instance(
                    getattr(args, "id", None), required=False
                ) if os.path.isfile(get_config_file_path()) else None
                inst = inst or {}
                docker_host = inst.get("dockerHost") or ""
                if "podman" in docker_host:
                    extra_vars["compose_command"] = "podman-compose"
                    extra_vars["inspect_command"] = "podman"
            except (OSError, IOError, KeyError):
                pass


def _add_host_targeting(ansible_args, host_value, vars_file_name):
    """Point ansible-playbook at --host (user@host accepted)."""
    # Parse user@host shorthand.
    host_spec = host_value
    ssh_user = None
    if "@" in host_spec:
        ssh_user, host_spec = host_spec.split("@", 1)

    # Update target_host in the vars file to use just the hostname,
    # not the user@host form (the hosts.yml or inline inventory
    # entry is keyed on hostname alone).
    with open(vars_file_name, "r") as f:
        _v = json.load(f)
    _v["target_host"] = host_spec
    with open(vars_file_name, "w") as f:
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


def _export_connection_env():
    """Export the SSH options and config dir ansible-playbook inherits."""
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
    # CANASTA_FORWARD_AGENT=no turns it off.
    #
    # ANSIBLE_SSH_ARGS replaces ansible.cfg's ssh_args outright, so the
    # connection-sharing options must be repeated here. Without them
    # every task opens a new SSH connection, and a phase of many short
    # tasks reaches sshd's connection-rate limits.
    ssh_args = (
        "-o StrictHostKeyChecking=accept-new "
        "-o UserKnownHostsFile=~/.ssh/known_hosts "
        "-o %s "
        # Long-running remote commands (gitops init's git push,
        # maintenance update) can outlast a NAT or
        # firewall idle timeout. ServerAliveInterval keeps the SSH
        # session warm so the parent doesn't see a "Broken pipe"
        # while the remote is still working.
        "-o ServerAliveInterval=30 -o ServerAliveCountMax=20"
    ) % forward_agent_option()
    control_dir = ssh_control_path_dir()
    if control_dir:
        ssh_args = "-o ControlMaster=auto -o ControlPersist=60s " + ssh_args
        os.environ.setdefault("ANSIBLE_SSH_CONTROL_PATH_DIR", control_dir)
    os.environ.setdefault("ANSIBLE_SSH_ARGS", ssh_args)

    # Hand the platform-correct config dir to Ansible. get_config_dir()
    # picks the macOS / Linux / root location; without exporting it,
    # YAML-side env lookups fall through to a Linux-only fallback and miss hosts.yml on
    # macOS. setdefault preserves any pre-set value (canasta-docker
    # sets it explicitly, and tests use it for isolation).
    os.environ.setdefault("CANASTA_CONFIG_DIR", get_config_dir())


def build_ansible_args(ansible_playbook, command_name, args, data,
                       extra=None):
    """Build the ansible-playbook command line.

    Extra vars are passed via a temp JSON file (-e @file) to avoid
    Jinja2 injection and shell quoting issues. `extra` adds vars the
    command's parameters do not carry.
    """
    _cleanup_stale_vars_files()

    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(command_name, {})
    params = cmd_def.get("parameters", [])

    # Build extra vars as a dict, written to a JSON file
    extra_vars = {"command": command_name}

    apply_instance_host_paths(cmd_def, args)

    # Pass target_host when the command declares --host. Commands
    # without a --host parameter resolve the target from the instance
    # registry via -i; argparse already rejects --host on those.
    host_value = getattr(args, "host", None)
    if host_value:
        extra_vars["target_host"] = host_value

    # The wrapper records its own absolute path in CANASTA_CLI_BIN so
    # playbooks that re-invoke the CLI (e.g. the crontab entry written by
    # 'backup schedule set') can use a fully-qualified path rather than
    # relying on a minimal cron PATH. Absent when canasta.py is run
    # directly without a wrapper.
    cli_bin = os.environ.get("CANASTA_CLI_BIN")
    if cli_bin:
        extra_vars["canasta_cli_bin"] = cli_bin

    # Declaring the runtime takes it out of the hands of the socket probe,
    # which can only report what it finds. A host carrying both runtimes has
    # no way to express which one its instances belong to otherwise.
    #
    # Only create reads this: every later command resolves the runtime from
    # the registry entry create wrote. main() has already rejected a value
    # that is neither runtime.
    runtime = os.environ.get("CANASTA_CONTAINER_RUNTIME", "").strip()
    if runtime:
        extra_vars["container_runtime"] = runtime

    if args.verbose:
        extra_vars["verbose"] = "true"
    else:
        os.environ["ANSIBLE_STDOUT_CALLBACK"] = "canasta_minimal"

    _add_param_extra_vars(params, args, host_value, extra_vars)
    if cmd_def.get("unfinished_pull_ok"):
        extra_vars["canasta_unfinished_pull_ok"] = "true"

    _add_runtime_extra_vars(args, host_value, extra_vars)
    extra_vars.update(extra or {})

    # Write vars to temp file (bypasses Jinja2 interpolation).
    # delete=False because ansible-playbook needs to read it after
    # execvp replaces this process. canasta.yml removes it once
    # ansible-playbook has loaded it; _cleanup_stale_vars_files() catches
    # a run that never got that far.
    vars_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="canasta-vars-",
        delete=False,
    )
    extra_vars["canasta_vars_file"] = vars_file.name
    json.dump(extra_vars, vars_file)
    vars_file.close()
    os.chmod(vars_file.name, 0o600)

    ansible_args = [
        ansible_playbook,
        CANASTA_YML,
        "-e", "@%s" % vars_file.name,
    ]

    if host_value:
        _add_host_targeting(ansible_args, host_value, vars_file.name)

    _export_connection_env()

    return ansible_args


def before_major_upgrade(ansible_playbook, command_name, args, data, major):
    """Upgrade every instance on this release, check them for `major`,
    then back each one up unless --skip-backup is given.

    True when the move to `major` can go ahead. A dry run upgrades and
    backs up nothing and only reports the checks.
    """
    dry_run = getattr(args, "dry_run", False)
    if not dry_run:
        print("Upgrading the instances on this release before moving to %d.x..."
              % major)
        sys.stdout.flush()
        rc = subprocess.call(build_ansible_args(
            ansible_playbook, command_name, args, data))
        if rc != 0:
            print("The upgrade on this release failed, so the CLI did not "
                  "move to %d.x." % major, file=sys.stderr)
            return False
    print("Checking the instances for %d.x..." % major)
    sys.stdout.flush()
    rc = subprocess.call(build_ansible_args(
        ansible_playbook, command_name, args, data,
        extra={"upgrade_preflight_major": major}))
    if rc != 0 or dry_run:
        return rc == 0
    if not getattr(args, "skip_backup", False):
        print("Backing up the instances before moving to %d.x..." % major)
        sys.stdout.flush()
        rc = subprocess.call(build_ansible_args(
            ansible_playbook, command_name, args, data,
            extra={"upgrade_backup_major": major}))
        if rc != 0:
            print("A backup failed, so the CLI did not move to %d.x." % major,
                  file=sys.stderr)
            return False
    record_major_move(major)
    return True


MAJOR_MOVE_FILE = "major_move"


def check_earlier_line_instances():
    """Stop an upgrade that would move an instance from a Canasta release of
    an earlier major to this CLI's major, unless the earlier major's CLI
    recorded that every instance passed the checks for this one.

    The CLI and the images share a version, so a release image of a lower
    major is an earlier line. A CLI that reached this major without the
    checks (an old Docker wrapper, an old --allow-major) stops here, before
    any instance moves.
    """
    try:
        with open(os.path.join(SCRIPT_DIR, "VERSION")) as f:
            major = _version_major(f.read())
    except OSError:
        return
    if major is None:
        return
    try:
        with open(os.path.join(get_config_dir(), MAJOR_MOVE_FILE)) as f:
            if f.read().strip() == str(major):
                return
    except OSError:
        pass
    earlier = []
    instances = _read_registry(get_config_file_path())
    for iid in sorted(instances):
        inst = instances[iid]
        image = _read_env_file(inst.get("path", ""),
                               inst.get("host") or "localhost").get("CANASTA_IMAGE", "")
        pin = canasta_image_pin.release_image(image)
        if pin and pin[1][0] < major:
            earlier.append("  %s: %s" % (iid, image))
    if not earlier:
        return
    print("Error: these instances run a Canasta release from before %d.x:\n%s\n"
          "The move to %d.x goes through the latest %d.x release of the CLI, "
          "which checks and backs up every instance first, and that did not "
          "happen. No instance was upgraded. Put the CLI back on the latest "
          "%d.x release and run 'canasta upgrade --allow-major' there. In "
          "Docker mode, write that version to %s and run "
          "'canasta upgrade --allow-major'."
          % (major, "\n".join(earlier), major, major - 1, major - 1,
             os.path.join(get_config_dir(), "cli_image_tag")),
          file=sys.stderr)
    sys.exit(1)


def record_major_move(major):
    """Record in the config dir that every instance passed the checks for
    `major`. The CLI of that major upgrades an instance from an earlier
    line only when this says so, so a move that skipped the checks stops
    there."""
    path = os.path.join(get_config_dir(), MAJOR_MOVE_FILE)
    try:
        with open(path, "w") as f:
            f.write("%d\n" % major)
    except OSError as e:
        print("Warning: could not record the checks for %d.x in %s (%s); "
              "the %d.x CLI will refuse to upgrade the instances until it "
              "can." % (major, path, e, major), file=sys.stderr)


def should_prompt_confirmation(cmd_def, yes_passed):
    """Whether to show the generic destructive-confirmation prompt.

    The net fires for a command that declares a "yes" parameter and was
    invoked without --yes.
    """
    has_yes_param = any(
        p["name"] == "yes" for p in cmd_def.get("parameters", [])
    )
    return has_yes_param and not yes_passed


def split_passthrough(remaining):
    """Split argv at the first ``--``.

    Returns ``(args_before, passthrough_tokens)``. The passthrough tokens are
    kept as a list so a quoted argument containing spaces (a SQL string via
    ``-e``, a ``--summary``, a page title) stays a single argv element; joining
    them into one string here would lose the boundaries a later shell-split
    cannot recover.
    """
    if "--" in remaining:
        idx = remaining.index("--")
        return remaining[:idx], remaining[idx + 1:]
    return remaining, []


CONTAINER_RUNTIMES = ("docker", "podman")


# ansible-core in requirements.txt needs it.
MIN_PYTHON = (3, 12)


def check_python_version(version_info=None):
    """Stop when this Python is older than the requirements need.

    pip cannot install the requirements into an older interpreter, so a venv
    created with one keeps running the CLI against dependencies that never
    update.
    """
    version_info = version_info or sys.version_info
    if tuple(version_info[:2]) >= MIN_PYTHON:
        return
    venv = os.path.join(SCRIPT_DIR, ".venv")
    needed = "%d.%d" % MIN_PYTHON
    print(
        "Error: the Canasta CLI needs Python {needed} or newer, but it is "
        "running on Python {found} ({exe}).\nRecreate its virtual environment "
        "with a newer Python:\n"
        "  python{needed} -m venv --clear {venv}\n"
        "  {venv}/bin/pip install -r {repo}/requirements.txt\n"
        "  {venv}/bin/ansible-galaxy collection install -r "
        "{repo}/requirements.yml".format(
            needed=needed,
            found=".".join(str(n) for n in version_info[:3]),
            exe=sys.executable, venv=venv, repo=SCRIPT_DIR),
        file=sys.stderr,
    )
    sys.exit(1)


def validate_container_runtime():
    """Reject an unrecognized CANASTA_CONTAINER_RUNTIME.

    In main() rather than on the Ansible dispatch: direct_commands never
    build extra vars, so a value checked there was accepted or rejected
    depending on which command happened to read it.
    """
    runtime = os.environ.get("CANASTA_CONTAINER_RUNTIME", "").strip()
    if runtime and runtime not in CONTAINER_RUNTIMES:
        print(
            "Error: CANASTA_CONTAINER_RUNTIME is '%s'. Valid values are %s."
            % (runtime, " and ".join("'%s'" % r for r in CONTAINER_RUNTIMES)),
            file=sys.stderr,
        )
        sys.exit(1)


def split_global_args(raw_args, data):
    """Split argv into the global flags and the rest.

    Returns (global_args, remaining, passthrough).
    """
    # Extract --verbose from args BEFORE the subcommand only. Prevents
    # "-v" in "maintenance exec -i x php -v" from being consumed as
    # --verbose; a pre-command --verbose goes into a global parser, a
    # post-command --verbose (long form) is also hoisted, and -v after
    # a subcommand is left alone so passthrough scripts keep their -v.
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

    # Handle -- separator for pass-through args. Consumers already accept a list
    # (the exec handler uses it directly; the Ansible path space-joins it to the
    # same string a string passthrough would have produced).
    remaining, passthrough = split_passthrough(remaining)
    return global_args, remaining, passthrough


def inject_passthrough(args, data, passthrough):
    """Put the args after -- into the command's first positional."""
    if not passthrough:
        return
    cmd_name = resolve_command_name(args) if args.command else None
    # Derive positional param name from definitions
    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(cmd_name, {})
    pos_params = [p["name"] for p in cmd_def.get("parameters", [])
                  if p.get("positional")]
    if pos_params:
        setattr(args, pos_params[0], passthrough)


def resolve_command_or_exit(parser, args, data):
    """The invoked command's internal name; prints help or an error and
    exits when there is none.
    """
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
        # Same for a bare nested group (e.g. 'canasta backup schedule'):
        # list its leaf subcommands.
        nested = nested_group_for(command_name)
        if nested:
            print_nested_subcommand_help(nested[0], nested[1], data)
            sys.exit(2)
        print("Unknown command: %s" % command_name, file=sys.stderr)
        sys.exit(1)
    return command_name


def validate_command_args(cmd_def, args):
    """Validate the parsed arguments before dispatch."""
    # CLI-layer parameter validation (required_unless, mutual exclusion,
    # named regex validators). Runs before Ansible so typos fail in
    # milliseconds; the playbook re-validates the same metadata as a
    # backstop. Exit on the first error in evaluation order.
    for code, message in collect_cli_param_errors(cmd_def, args):
        print(message, file=sys.stderr)
        sys.exit(code)


def run_preflight_checks(command_name, args):
    """Refusals that apply before either the direct or the Ansible path."""
    check_root_in_foreign_config_dir()
    warn_root_on_shared_install()

    # Pre-flight: a duplicate instance ID is an expected refusal, not a
    # crash. Catch it here so it exits with EXIT_ALREADY_EXISTS instead
    # of the opaque Ansible task-failure code (the playbook still checks
    # as a backstop).
    if command_name == "create":
        check_create_precondition(args)

    # Pre-flight: the Docker-mode CLI cannot drive a Podman instance that
    # is local to this controller. Refuse before either path starts, so
    # the operator gets the reason instead of a mid-operation rc=127.
    # `install` is exempt: it never drives an instance's compose stack —
    # it provisions host-level software, and a local install cannot run
    # from the container. See check_docker_mode_install_target below.
    if command_name != "install":
        check_docker_mode_can_reach_runtime(args)

    # Pre-flight: `canasta install` provisions software on the target
    # host, which the container cannot do for the controller itself.
    # Refuse local installs (no target, localhost, or -i resolving to a
    # local instance) up front; remote targets run over SSH on the
    # target.
    if command_name == "install":
        check_docker_mode_install_target(args)

    # Pre-flight: a gitops init key exported outside a mounted directory
    # is lost with the container, after the repository has been pushed.
    if command_name == "gitops_init":
        check_docker_mode_key_path(args)

    # Interactive exec: bypass Ansible for TTY support.
    if command_name == "maintenance_exec":
        if handle_interactive_exec(args):
            # handle_interactive_exec calls os.execvp and never returns
            # when it handles the command; returns False to fall through
            # to Ansible for the service-listing case.
            pass


def run_direct_path(command_name, cmd_def, cmd_index, args, lock_fd):
    """Run the command on the direct path when it has a handler.

    Exits with the handler's result; returns the (command name, definition)
    to hand to Ansible otherwise, which an AnsibleHandoff may change.
    """
    # Direct command bypass: run simple commands without Ansible overhead.
    # direct_only commands always run via direct_commands and never fall
    # through to Ansible — CANASTA_FORCE_ANSIBLE has no effect on them
    # because they have no playbook. A handler may still hand the rest of
    # its run to another command's playbook (AnsibleHandoff).
    direct_only = cmd_def.get("direct_only", False)
    if direct_only or not os.environ.get("CANASTA_FORCE_ANSIBLE"):
        import direct_commands
        if direct_commands.is_direct_command(command_name):
            result = direct_commands.run_direct_command(command_name, args)
            if isinstance(result, direct_commands.AnsibleHandoff):
                command_name = result.command
                cmd_def = cmd_index.get(command_name, {})
                # A second flock on the held lock file would refuse this
                # run as busy, so keep the lock the first command took.
                if lock_fd is None:
                    acquire_instance_lock(command_name, args)
            elif result is not direct_commands.FALLBACK:
                sys.exit(result)
            elif direct_only:
                print(
                    "Error: command '%s' is direct_only but its handler "
                    "returned FALLBACK — no Ansible playbook to fall back to."
                    % command_name,
                    file=sys.stderr,
                )
                sys.exit(1)
    return command_name, cmd_def


def confirm_destructive_command(cmd_def, command_name, args):
    """Ask before running a command that has a --yes parameter without it."""
    # If the command defines a "yes" parameter and the user did not pass it,
    # prompt interactively rather than making them re-run with --yes.
    if should_prompt_confirmation(cmd_def, getattr(args, "yes", False)):
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
            print("\nOperation canceled.")
            sys.exit(1)
        if answer.strip().lower() != "y":
            print("Operation canceled.")
            sys.exit(0)
        # Tell the playbook to skip its own confirmation check
        args.yes = True


def main():
    check_python_version()
    validate_container_runtime()
    data = load_definitions()
    parser = build_parser(data)

    global_args, remaining, passthrough = split_global_args(
        sys.argv[1:], data)

    # Parse with the full parser (subcommands + flags)
    args = parser.parse_args(remaining)

    # Merge global verbose into args
    args.verbose = global_args.verbose or args.verbose

    inject_passthrough(args, data, passthrough)
    hoist_flags_from_remainder(args)

    command_name = resolve_command_or_exit(parser, args, data)
    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(command_name, {})

    validate_command_args(cmd_def, args)
    # Before the direct-command dispatch, so both paths read these the same.
    apply_instance_host_paths(cmd_def, args)

    refuse_kubernetes(command_name, cmd_def, args)
    run_preflight_checks(command_name, args)

    lock_fd = acquire_instance_lock(command_name, args)

    command_name, cmd_def = run_direct_path(
        command_name, cmd_def, cmd_index, args, lock_fd)

    confirm_destructive_command(cmd_def, command_name, args)

    ansible_playbook = find_ansible_playbook()
    ansible_args = build_ansible_args(
        ansible_playbook, command_name, args, data
    )

    # Run the self-update before exec so the playbook process sees
    # the pulled code (ansible.cfg, module_utils, modules, playbooks)
    # at startup. Used to be the first task of upgrade.yml itself,
    # but running it inside the playbook means ansible-playbook's
    # in-memory config goes stale on cfg changes.
    if command_name == "upgrade":
        # Set by the canasta-docker wrapper for 'upgrade --allow-major' to
        # a higher major: this run only upgrades, checks and backs up the
        # instances, and the wrapper moves the CLI if it succeeds.
        step_major = os.environ.get("CANASTA_MAJOR_STEP")
        if step_major:
            sys.exit(0 if before_major_upgrade(
                ansible_playbook, command_name, args, data,
                int(step_major)) else 1)
        self_update_cli(
            dev=getattr(args, "dev", False),
            allow_major=getattr(args, "allow_major", False),
            dry_run=getattr(args, "dry_run", False),
            before_major=lambda major: before_major_upgrade(
                ansible_playbook, command_name, args, data, major),
        )
        check_earlier_line_instances()

    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(ansible_args[0], ansible_args)


if __name__ == "__main__":
    main()
