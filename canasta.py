#!/usr/bin/env python3
"""Canasta CLI wrapper -- translates CLI invocations into ansible-playbook calls.

Reads command definitions from meta/command_definitions.yml and builds
argparse subcommands with proper type-aware flag parsing, replacing the
original bash wrapper script.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFINITIONS_PATH = os.path.join(SCRIPT_DIR, "meta", "command_definitions.yml")
CANASTA_YML = os.path.join(SCRIPT_DIR, "canasta.yml")
ANSIBLE_CFG = os.path.join(SCRIPT_DIR, "ansible.cfg")

# Registry-location and lookup helpers are shared with the
# canasta_registry Ansible module (which reads/writes conf.json), so the
# CLI and the module never disagree about where the registry lives. The
# helpers live under the role's module_utils dir; append it to the path
# so `import canasta_config` resolves the same file Ansible ships.
sys.path.append(os.path.join(SCRIPT_DIR, "roles", "common", "module_utils"))
import canasta_config  # noqa: E402

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
    if ( cafile and path_exists( cafile ) ) or ( capath and path_exists( capath ) ):
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
    "config": ["get", "set", "unset", "regenerate", "refresh-template"],
    "extension": ["list", "enable", "disable"],
    "skin": ["list", "enable", "disable"],
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
        "init", "join", "add", "rm", "push", "pull",
        "status", "diff", "fix-submodules", "sync",
    ],
    "storage": ["setup", "list"],
    "host": ["add", "remove", "list"],
    "argocd": ["ui", "password", "apps"],
    "sidecar": ["add", "list", "remove", "migrate"],
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


def _hostname_hint(value):
    """Extra guidance when a rejected hostname carries a scheme or a port —
    the most common reason --domain-name validation fails (users expect to
    pass a URL or host:port). Ports/schemes are configured elsewhere."""
    text = str(value)
    if "://" in text or re.search(r":[0-9]+$", text):
        return (
            " Do not include a scheme or port here: pass --domain-name as a "
            "bare hostname (e.g. 'example.com') and set HTTP_PORT/HTTPS_PORT "
            "(and CADDY_AUTO_HTTPS=off for HTTP-only) in your .env file."
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
        _hostname_hint,
    ),
}


# Parameter validation runs at two layers by design. These functions are
# the CLI's fast-fail half: they run after argparse but before any Ansible
# work, so a typo (comma-vs-period domain, a missing required_unless
# partner) fails in milliseconds instead of after Argo CD/helm/image-pull
# steps have already done work. The playbook re-validates the same
# command_definitions.yml metadata in roles/common/tasks/validate_params.yml
# so values that bypass the CLI (CANASTA_FORCE_ANSIBLE, Molecule, a raw
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


def _validate_orchestrator_only(cmd_def, get_value):
    """Flag params restricted to one orchestrator when another is selected."""
    errors = []
    orchestrator = get_value("orchestrator")
    if not orchestrator:
        return errors
    for param in cmd_def.get("parameters", []):
        orch_only = param.get("orchestrator_only")
        if not orch_only:
            continue
        value = get_value(param["name"])
        if value is None or value == param.get("default"):
            continue
        if orchestrator != orch_only:
            errors.append((
                1,
                "Error: --%s can only be used with --orchestrator %s"
                % (param["name"].replace("_", "-"), orch_only),
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

    `args` is the parsed argparse Namespace; orchestrator-alias
    normalization (k8s -> kubernetes) is expected to have run already.
    """
    def get_value(name):
        return getattr(args, name, None)

    return (
        _validate_required_unless(cmd_def, get_value)
        + _validate_orchestrator_only(cmd_def, get_value)
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


_RELEASE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _pick_latest_release_tag(tags, max_major=None):
    """Return the highest strict vX.Y.Z tag from `tags`, or None.

    Pre-releases (e.g. v5.0.0-rc1) and non-version tags are ignored so we
    only ever move to a real release. Comparison is by numeric (major,
    minor, patch) tuple, not lexical, so v4.0.10 sorts above v4.0.9.

    When `max_major` is set, tags whose major exceeds it are skipped, so
    the result never crosses into a higher major version than the caller
    permits (the default `canasta upgrade` stays within the current major).
    """
    best = None
    best_key = None
    for tag in tags:
        m = _RELEASE_TAG_RE.match(tag.strip())
        if not m:
            continue
        key = tuple(int(p) for p in m.groups())
        if max_major is not None and key[0] > max_major:
            continue
        if best_key is None or key > best_key:
            best, best_key = tag.strip(), key
    return best


def _version_major(version):
    """Return the integer major component of a vX.Y.Z / X.Y.Z string, or None."""
    m = re.match(r"^v?(\d+)\.", version.strip())
    return int(m.group(1)) if m else None


def self_update_cli(dev=False, allow_major=False):
    """Update the CLI code before invoking ansible-playbook.

    By default the CLI moves to the latest released version that does not
    cross into a higher major than the one currently installed — the
    highest vX.Y.Z git tag whose major matches the current VERSION — via a
    detached checkout. Pass ``allow_major=True`` to lift that cap and move
    to the latest release overall (which may be a new major with breaking
    changes). Pass ``dev=True`` to instead track the head of main
    (``git checkout main && git pull --ff-only``); ``allow_major`` has no
    effect with ``dev``.
    Release tags are used rather than the GitHub Releases API because the
    Releases API deliberately keeps v3.7.0 as 'latest' for legacy clients
    (see .github/workflows/docker.yml).

    Runs in the canasta.py process (i.e. before os.execvp swaps in
    ansible-playbook), so any changes the move lands on disk —
    ansible.cfg, module_utils, modules, playbook tasks — are visible
    to the ansible-playbook process from its very first config load.
    Used to live as the first task of upgrade.yml itself, but loading
    ansible.cfg at startup means an in-flight cfg change between
    pre- and post-move state would silently make ansible-playbook
    keep using stale values (#489).

    Docker installs (no .git in the script dir) are no-ops here: the
    canasta-docker wrapper has already pulled the image before this
    process started.
    """
    if os.environ.get("CANASTA_SELF_UPDATED"):
        # This process is the re-exec spawned by the first invocation
        # after it pulled (see os.execv at the end of this function).
        # The code on disk is already current and settled, so skip the
        # update check entirely — including its "already up to date"
        # line — and let this fresh process run the command.
        return

    repo = SCRIPT_DIR
    if not os.path.isdir(os.path.join(repo, ".git")):
        return  # Docker install — image pull happened in the wrapper.

    def _git(args, timeout=30, check=False):
        return subprocess.run(
            ["git"] + args, cwd=repo,
            capture_output=True, text=True, timeout=timeout, check=check,
        )

    try:
        current_commit = _git(
            ["rev-parse", "--short", "HEAD"], timeout=5, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            OSError) as e:
        print("Warning: could not check current commit: %s" % e,
              file=sys.stderr)
        return

    try:
        with open(os.path.join(repo, "VERSION")) as f:
            current_version = f.read().strip()
    except OSError:
        current_version = "unknown"

    try:
        _git(["fetch", "--tags", "origin"], check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            OSError) as e:
        # `e.stderr` is bytes/None on TimeoutExpired/OSError — guard.
        detail = (getattr(e, "stderr", "") or str(e)).strip()
        msg = (
            "WARNING: self-update skipped — could not fetch from origin "
            "(%s).\nThe CLI was NOT updated; this run uses the existing "
            "version (%s, %s)." % (detail, current_version, current_commit)
        )
        if "ermission denied" in detail:
            msg += (
                "\nThe install dir (%s) is not writable by you — you are "
                "likely not in the 'canasta' group. Fix with:\n"
                "  sudo usermod -aG canasta $(id -un)\n"
                "then log out and back in (or run 'newgrp canasta') and "
                "re-run." % repo
            )
        print(msg, file=sys.stderr)
        return

    # Resolve the ref this run should move to: head of main for --dev, or
    # the latest release tag otherwise. The release pick is capped to the
    # current major so 'canasta upgrade' never crosses a major boundary;
    # --allow-major (allow_major=True) lifts the cap to the latest overall.
    major_hint = ""
    if dev:
        target_ref = "origin/main"
    else:
        tags = _git(["tag", "-l", "v*"], timeout=10).stdout.split()
        max_major = None if allow_major else _version_major(current_version)
        target_ref = _pick_latest_release_tag(tags, max_major=max_major)
        latest_overall = _pick_latest_release_tag(tags)
        if target_ref is None:
            if latest_overall is not None:
                # Tags exist but all sit above the current major and the cap
                # is in effect — point the operator at --allow-major.
                print(
                    "WARNING: self-update skipped — no release found at or "
                    "below the current major (v%s.x); the latest release is "
                    "%s.\nRun 'canasta upgrade --allow-major' to move to it "
                    "(may include breaking changes), or 'canasta upgrade "
                    "--dev' to track the development branch."
                    % (max_major, latest_overall),
                    file=sys.stderr,
                )
            else:
                print(
                    "WARNING: self-update skipped — no release tags found.\n"
                    "Use 'canasta upgrade --dev' to track the development "
                    "branch (head of main) instead.",
                    file=sys.stderr,
                )
            return
        if latest_overall and latest_overall != target_ref:
            # A newer release exists beyond the current-major cap.
            major_hint = (
                "\nA newer major release (%s) is available. Run 'canasta "
                "upgrade --allow-major' to move to it (may include breaking "
                "changes)." % latest_overall
            )

    target_commit = _git(
        ["rev-parse", "--short", target_ref], timeout=5,
    ).stdout.strip()

    if dev:
        on_main = _git(
            ["rev-parse", "--abbrev-ref", "HEAD"], timeout=5,
        ).stdout.strip() == "main"
        # Already on main at origin/main? Nothing to do. (Require being on
        # the main branch so a prior release upgrade's detached HEAD still
        # switches back.)
        if target_commit and target_commit == current_commit and on_main:
            print(
                "Canasta CLI is already up to date "
                "(version %s, commit %s, dev (main))."
                % (current_version, current_commit)
            )
            return
        # Restore the main branch first — a previous release upgrade may
        # have left a detached HEAD at a tag — then fast-forward.
        co = _git(["checkout", "main"], timeout=30)
        pull = _git(["pull", "--ff-only", "origin", "main"], timeout=60)
        move_failed = co.returncode != 0 or pull.returncode != 0
    else:
        # Release channel: only ever move forward. If the latest release
        # tag is already contained in HEAD — we're sitting on it, or on a
        # newer development build — checking it out would be a downgrade,
        # so stay put. 'canasta upgrade' must never travel backward in time.
        contained = _git(
            ["merge-base", "--is-ancestor", target_ref, "HEAD"], timeout=5,
        ).returncode == 0
        if contained:
            if target_commit == current_commit:
                print(
                    "Canasta CLI is already up to date "
                    "(version %s, commit %s, release %s).%s"
                    % (current_version, current_commit, target_ref, major_hint)
                )
            else:
                print(
                    "Canasta CLI is already ahead of the latest release "
                    "(%s); keeping the current build (version %s, commit %s) "
                    "rather than moving backward.\nUse 'canasta upgrade --dev' "
                    "to keep tracking development builds, or re-run the "
                    "installer at https://get.canasta.wiki to return to the "
                    "latest release.%s"
                    % (target_ref, current_version, current_commit, major_hint)
                )
            return
        # HEAD is behind the latest release (or on a divergent line that
        # doesn't contain it) — move to the release tag.
        co = _git(["checkout", target_ref], timeout=60)
        move_failed = co.returncode != 0

    if move_failed:
        print(
            "Could not move to %s "
            "(local checkout may have uncommitted changes or have "
            "diverged).\nResolve manually in %s (e.g. check 'git status')."
            % (target_ref, repo),
            file=sys.stderr,
        )
        return

    new_commit = _git(
        ["rev-parse", "--short", "HEAD"], timeout=5,
    ).stdout.strip() or "unknown"
    new_date = _git(
        ["log", "-1", "--format=%cd",
         "--date=format:%Y-%m-%d %H:%M:%S"],
        timeout=5,
    ).stdout.strip() or "unknown"
    try:
        with open(os.path.join(repo, "VERSION")) as f:
            new_version = f.read().strip()
    except OSError:
        new_version = "unknown"

    # BUILD_COMMIT/BUILD_DATE drive `canasta version` output for
    # native installs. Update them in lockstep with the pull so a
    # subsequent `canasta version` reflects the just-pulled commit.
    for filename, value in (
        ("BUILD_COMMIT", new_commit),
        ("BUILD_DATE", new_date),
    ):
        try:
            with open(os.path.join(repo, filename), "w") as f:
                f.write(value + "\n")
        except OSError as e:
            print(
                "Warning: could not update %s: %s" % (filename, e),
                file=sys.stderr,
            )

    # Refresh Python and Ansible deps in case requirements.txt or
    # requirements.yml was bumped in the pull. Without this, dep pins
    # added to the new code don't actually land — operators see new
    # code with stale collections (Helm 4 / kubernetes.core 6.4 was
    # the last instance) and hit cryptic playbook errors.
    def _refresh_deps(label, cmd, timeout):
        try:
            subprocess.run(cmd, check=True, timeout=timeout)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                OSError) as e:
            print(
                "Warning: %s failed (%s).\nRe-run manually:\n  %s"
                % (label, e, " ".join(cmd)),
                file=sys.stderr,
            )
            return False

    pip_ok = _refresh_deps(
        "pip install -r requirements.txt",
        [sys.executable, "-m", "pip", "install", "--quiet",
         "-r", os.path.join(repo, "requirements.txt")],
        timeout=180,
    )
    galaxy = os.path.join(os.path.dirname(sys.executable), "ansible-galaxy")
    galaxy_ok = _refresh_deps(
        "ansible-galaxy collection install",
        [galaxy, "collection", "install", "--upgrade",
         "-r", os.path.join(repo, "requirements.yml")],
        timeout=300,
    )

    print(
        "Updated Canasta CLI from %s (%s) to %s (%s).%s"
        % (current_version, current_commit, new_version, new_commit, major_hint)
    )
    if not (pip_ok and galaxy_ok):
        print(
            "Warning: dependency refresh incomplete; see above. "
            "Instances may still be upgraded against stale deps.",
            file=sys.stderr,
        )

    # The pull just rewrote this process's own ansible roles, modules,
    # and module_utils on disk. Continuing to ansible-playbook in the
    # very process that did the pulling intermittently fails to resolve
    # a freshly written module_utils. Re-exec the CLI so the rest of the
    # command runs in a clean process against fully-settled code;
    # the CANASTA_SELF_UPDATED guard at the top stops the re-exec'd
    # process from looping back through the update.
    os.environ["CANASTA_SELF_UPDATED"] = "1"
    os.execv(
        sys.executable,
        [sys.executable, os.path.abspath(__file__)] + sys.argv[1:],
    )


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
                    # `required: true` on a positional multi means at least
                    # one value must be given (argparse errors on none).
                    nargs="+" if required else "*",
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

    Delegates to the shared helper so the CLI resolves the same location
    the canasta_registry module writes to.
    """
    return canasta_config.get_config_dir()


def get_config_file_path():
    """Return the path to conf.json."""
    return canasta_config.config_path(get_config_dir())


def resolve_instance(instance_id=None):
    """Resolve an instance from the registry by ID or working directory.

    Returns a dict with id, path, orchestrator keys, or exits on error.
    """
    conf_file = get_config_file_path()
    if not os.path.isfile(conf_file):
        print("Error: no registry found at %s" % conf_file, file=sys.stderr)
        sys.exit(1)
    instances = canasta_config.read_config(get_config_dir()).get("Instances", {})

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

    # Walk up from cwd to find a matching instance path. Honor
    # CANASTA_HOST_PWD (the dockerized CLI passes the host's working
    # directory there) before falling back to the process cwd.
    search = os.environ.get("CANASTA_HOST_PWD") or os.getcwd()
    iid, inst = canasta_config.find_by_path(instances, search)
    if iid is not None:
        inst["id"] = iid
        return inst

    print(
        "Error: no instance found for current directory", file=sys.stderr
    )
    sys.exit(1)


def _redirect_stdin_from_file(path):
    """Point fd 0 at `path` so the about-to-be-exec'd command reads it as
    stdin. Used by `maintenance exec --stdin-file`. No-op when path is unset.

    This runs in the direct-exec path (handle_interactive_exec os.execvp's
    docker/kubectl/ssh, replacing this process), so there is no shell to do
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
    """Handle maintenance exec by running docker/kubectl exec directly.

    Dispatch rules:
      - No -s, no command  -> list services (fall through to Ansible)
      - No -s, with command -> exec in web service
      - -s given, no command -> interactive /bin/bash in that service
      - -s given, with command -> exec in that service
    """
    import shlex
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
    orchestrator = inst.get("orchestrator", "compose")

    if not service:
        service = "web"
    if not command:
        # Interactive shell: prefer bash, but fall back to sh so a slim sidecar
        # image (alpine, *-slim) without bash still opens a usable shell. A
        # command (the else branch above) is exec'd directly and needs no shell.
        command = ["/bin/sh", "-c",
                   "if command -v bash >/dev/null 2>&1; then exec bash; "
                   "else exec sh; fi"]

    if orchestrator in ("kubernetes", "k8s"):
        # Find the pod for this service
        ns = "canasta-%s" % inst["id"]
        # Select only a Running pod, matching roles/orchestrator/tasks/
        # k8s_get_pod.yml. Without the phase filter, items[0] during a
        # rollout/scale/crash-loop can be a Pending/Terminating/Failed pod,
        # so the exec would target the wrong replica or fail outright.
        result = subprocess.run(
            [
                "kubectl", "get", "pods", "-n", ns,
                "-l", "app.kubernetes.io/component=%s" % service,
                "--field-selector=status.phase=Running",
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
        # With --stdin-file the exec is non-interactive: keep stdin open
        # (-i) but drop the TTY (-t), which would otherwise mangle a piped
        # payload. Without it, preserve the interactive -it behavior.
        stdin_file = getattr(args, "stdin_file", None)
        tty_flags = "-i" if stdin_file else "-it"
        exec_args = ["kubectl", "exec", tty_flags, pod, "-n", ns, "--"]
        exec_args.extend(command)
        _redirect_stdin_from_file(stdin_file)
        try:
            os.execvp("kubectl", exec_args)
        except FileNotFoundError:
            print("Error: kubectl not found on PATH", file=sys.stderr)
            sys.exit(1)
    else:
        host = inst.get("host", "localhost")
        # With --stdin-file the exec must be non-interactive so the piped
        # payload reaches the command: `docker compose exec -T` (no TTY).
        # Without it, omit -T to preserve the interactive shell behavior.
        stdin_file = getattr(args, "stdin_file", None)
        docker_cmd = ["docker", "compose", "exec"]
        if stdin_file:
            docker_cmd.append("-T")
        docker_cmd += [service] + command
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
            try:
                _redirect_stdin_from_file(stdin_file)
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


def nested_group_for(command_name):
    """If command_name names a bare nested group (e.g. 'backup_schedule' or
    'storage_setup'), return (group, nested_group); otherwise None.

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

    # The wrapper records its own absolute path in CANASTA_CLI_BIN so
    # playbooks that re-invoke the CLI (e.g. the crontab entry written by
    # 'backup schedule set') can use a fully-qualified path rather than
    # relying on a minimal cron PATH. Absent when canasta.py is run
    # directly without a wrapper.
    cli_bin = os.environ.get("CANASTA_CLI_BIN")
    if cli_bin:
        extra_vars["canasta_cli_bin"] = cli_bin

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


def should_prompt_confirmation(cmd_def, yes_passed):
    """Whether to show the generic destructive-confirmation prompt.

    The net fires for a command that declares a "yes" parameter and was
    invoked without --yes. A command that is read-only unless an explicit
    write flag is given (so prompting before it has done anything is
    wrong) opts out with 'self_confirm: true' and does its own gating in
    the playbook.
    """
    if cmd_def.get("self_confirm"):
        return False
    has_yes_param = any(
        p["name"] == "yes" for p in cmd_def.get("parameters", [])
    )
    return has_yes_param and not yes_passed


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
        # Same for a bare nested group (e.g. 'canasta backup schedule',
        # 'canasta storage setup'): list its leaf subcommands.
        nested = nested_group_for(command_name)
        if nested:
            print_nested_subcommand_help(nested[0], nested[1], data)
            sys.exit(2)
        print("Unknown command: %s" % command_name, file=sys.stderr)
        sys.exit(1)

    cmd_index = {c["name"]: c for c in data["commands"]}
    cmd_def = cmd_index.get(command_name, {})

    # Normalize orchestrator alias: k8s → kubernetes.
    if getattr(args, "orchestrator", None) == "k8s":
        args.orchestrator = "kubernetes"

    # CLI-layer parameter validation (required_unless, orchestrator_only,
    # named regex validators). Runs before Ansible so typos fail in
    # milliseconds; the playbook re-validates the same metadata as a
    # backstop. Exit on the first error in evaluation order.
    for code, message in collect_cli_param_errors(cmd_def, args):
        print(message, file=sys.stderr)
        sys.exit(code)

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
    # Commands that are read-only by default opt out with 'self_confirm'.
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

    ansible_playbook = find_ansible_playbook()
    ansible_args = build_ansible_args(
        ansible_playbook, command_name, args, data
    )

    # Run the self-update before exec so the playbook process sees
    # the pulled code (ansible.cfg, module_utils, modules, playbooks)
    # at startup. Used to be the first task of upgrade.yml itself,
    # but running it inside the playbook means ansible-playbook's
    # in-memory config goes stale on cfg changes — see #489.
    if command_name == "upgrade":
        self_update_cli(
            dev=getattr(args, "dev", False),
            allow_major=getattr(args, "allow_major", False),
        )

    os.execvp(ansible_args[0], ansible_args)


if __name__ == "__main__":
    main()
