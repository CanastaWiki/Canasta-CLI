"""list, version, status — info commands."""

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from . import _helpers
from ._helpers import register


def _gather_all_instances(instances):
    """Gather info for all instances in parallel."""
    if not instances:
        return []

    ordered_ids = list(instances.keys())
    results = {}

    with ThreadPoolExecutor(max_workers=len(instances)) as pool:
        futures = {
            pool.submit(_helpers._gather_instance_info, iid, inst): iid
            for iid, inst in instances.items()
        }
        for future in as_completed(futures):
            iid = futures[future]
            try:
                results[iid] = future.result()
            except Exception:
                inst = instances[iid]
                results[iid] = _helpers._make_detail(
                    iid,
                    inst.get("host") or "localhost",
                    inst.get("path", ""),
                    inst.get("orchestrator", "compose"),
                    "ERROR",
                    [],
                )

    return [results[iid] for iid in ordered_ids]


def _classify_for_cleanup(inst_id, inst):
    """Classify an instance for cleanup purposes.

    Returns one of:
      'exists'      — the instance directory is present on its host
      'missing'     — the host is reachable and confirmed the path is gone
      'unreachable' — SSH transport failure (DNS, connection refused,
                      timeout, auth, host key) — no information about
                      whether the instance actually still exists
    """
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")

    if _helpers._is_localhost(host):
        return "exists" if os.path.isdir(path) else "missing"

    # OpenSSH returns rc=255 specifically for transport-layer failures.
    # Any other non-zero rc means the remote command ran and failed on
    # its own terms — for 'test -d' that means the directory really is
    # missing on the remote host.
    rc, _ = _helpers._ssh_run(host, "test -d %s" % _helpers._shell_quote(path))
    if rc == 0:
        return "exists"
    if rc == 255:
        return "unreachable"
    return "missing"


@register("list")
def cmd_list(args):
    config_dir = _helpers._get_config_dir()
    conf_path = os.path.join(config_dir, "conf.json")

    if getattr(args, "cleanup", False):
        instances = _helpers._read_registry(conf_path)
        force = getattr(args, "force", False)
        dry_run = getattr(args, "dry_run", False)

        # Probe all instances in parallel — remote classify can SSH.
        classifications = {}
        if instances:
            with ThreadPoolExecutor(max_workers=len(instances)) as pool:
                futures = {
                    pool.submit(_classify_for_cleanup, iid, inst): iid
                    for iid, inst in instances.items()
                }
                for future in as_completed(futures):
                    iid = futures[future]
                    try:
                        classifications[iid] = future.result()
                    except Exception:
                        # On unexpected error treat as unreachable so we
                        # don't drop the entry by surprise.
                        classifications[iid] = "unreachable"

        missing = sorted(
            iid for iid, c in classifications.items() if c == "missing"
        )
        unreachable = sorted(
            iid for iid, c in classifications.items() if c == "unreachable"
        )

        if dry_run:
            print("Dry run — no changes made.")
            print("Would remove (confirmed missing): %s"
                  % (", ".join(missing) if missing else "(none)"))
            if force:
                print("Would remove (unreachable, --force): %s"
                      % (", ".join(unreachable) if unreachable else "(none)"))
            else:
                print("Skipped (unreachable; pass --force to remove): %s"
                      % (", ".join(unreachable) if unreachable else "(none)"))
        else:
            to_remove = list(missing) + (list(unreachable) if force else [])
            if to_remove:
                for iid in to_remove:
                    del instances[iid]
                _helpers._write_registry(conf_path, instances)
                print("Removed stale entries: %s" % ", ".join(to_remove))
            if unreachable and not force:
                plural = "y" if len(unreachable) == 1 else "ies"
                print(
                    "Kept %d unreachable entr%s (pass --force to remove): %s"
                    % (len(unreachable), plural, ", ".join(unreachable))
                )

    instances = _helpers._read_registry(conf_path)
    host_filter = getattr(args, "host", None)
    instances = _helpers._filter_by_host(instances, host_filter)

    if not instances:
        print("No Canasta instances.")
        return 0

    details = _gather_all_instances(instances)

    _helpers._print_table(details)
    return 0

def _read_instance_image(inst_id, inst):
    """Return 'CANASTA_IMAGE (running: X)' for a single instance entry.

    'running' comes from /tmp/canasta-version inside the web container;
    absent when the container isn't up or the command fails for any
    reason. Never raises.
    """
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    env_vars = _helpers._read_env_file(path, host)
    image = env_vars.get("CANASTA_IMAGE", "(unset)")

    # Running Canasta version — line 2 of /tmp/canasta-version inside
    # the web container. Best effort; fall back silently.
    running = "(not running)"
    compose_cmd = (
        "cd %s && docker compose exec -T web sh -c "
        "\"cat /tmp/canasta-version 2>/dev/null | sed -n '2p'\" 2>/dev/null"
        % _helpers._shell_quote(path)
    )
    if _helpers._is_localhost(host):
        try:
            result = subprocess.run(
                ["sh", "-c", compose_cmd],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                running = result.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            pass
    else:
        rc, stdout = _helpers._ssh_run(host, compose_cmd)
        if rc == 0 and stdout.strip():
            running = stdout.strip()

    return image, running


@register("version")
def cmd_version(args):
    script_dir = _helpers._get_script_dir()

    version_file = os.path.join(script_dir, "VERSION")
    try:
        with open(version_file) as f:
            version = f.read().strip()
    except OSError:
        version = "unknown"

    # Target Canasta version — what this CLI was built to deploy.
    # Always shown, even when no instances are registered. This is the
    # CANASTA_VERSION file bundled with the Canasta CLI, distinct from
    # any particular instance's pinned CANASTA_IMAGE tag.
    target_version_file = os.path.join(script_dir, "CANASTA_VERSION")
    try:
        with open(target_version_file) as f:
            target_canasta_version = f.read().strip()
    except OSError:
        target_canasta_version = "unknown"

    # Run mode is set by the canasta-docker wrapper. Presence of
    # BUILD_COMMIT isn't a reliable signal — native installs also
    # write it (via 'make build-info') so 'canasta version' works
    # outside a git checkout.
    mode = "docker" if os.environ.get("CANASTA_RUN_MODE") == "docker" else "native"

    build_commit_file = os.path.join(script_dir, "BUILD_COMMIT")
    if os.path.isfile(build_commit_file):
        try:
            with open(build_commit_file) as f:
                commit = f.read().strip()
            with open(os.path.join(script_dir, "BUILD_DATE")) as f:
                date = f.read().strip()
        except OSError:
            commit = "unknown"
            date = "unknown"
    else:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=script_dir, capture_output=True, text=True, timeout=5,
            )
            commit = result.stdout.strip() if result.returncode == 0 else "unknown"
        except (subprocess.TimeoutExpired, OSError):
            commit = "unknown"
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M:%S"],
                cwd=script_dir, capture_output=True, text=True, timeout=5,
            )
            date = result.stdout.strip() if result.returncode == 0 else "unknown"
        except (subprocess.TimeoutExpired, OSError):
            date = "unknown"

    print("Canasta CLI v%s (%s, commit %s, built %s)" % (version, mode, commit, date))
    print("Target Canasta version: %s" % target_canasta_version)

    # --cli-only: stop after the two-line header; no instance reads.
    if getattr(args, "cli_only", False):
        return 0

    inst_id = getattr(args, "id", None)

    if inst_id:
        # Explicit -i: full-fidelity report (image tag + running runtime).
        inst_id, inst = _helpers._resolve_instance(args)
        image, running = _read_instance_image(inst_id, inst)
        print("Instance '%s': %s (running: %s)" % (inst_id, image, running))
        return 0

    # No -i: try cwd resolution. If inside an instance directory,
    # give the same full-fidelity report as -i.
    config_dir = _helpers._get_config_dir()
    conf_path = os.path.join(config_dir, "conf.json")
    instances = _helpers._read_registry(conf_path)
    cwd = os.path.abspath(os.getcwd())
    while True:
        for iid, inst in instances.items():
            if os.path.abspath(inst.get("path", "")) == cwd:
                image, running = _read_instance_image(iid, inst)
                print("Instance '%s': %s (running: %s)" % (iid, image, running))
                return 0
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    # Outside any instance directory: list every registered instance
    # with its pinned CANASTA_IMAGE tag only (no docker-compose-exec
    # query for the running version). Keeps the default fast even with
    # many registered instances across remote hosts.
    host_filter = getattr(args, "host", None)
    instances = _helpers._filter_by_host(instances, host_filter)
    if not instances:
        print("No instances registered.")
        return 0
    for iid in sorted(instances.keys()):
        host = instances[iid].get("host") or "localhost"
        path = instances[iid].get("path", "")
        env_vars = _helpers._read_env_file(path, host)
        image = env_vars.get("CANASTA_IMAGE", "(unset)")
        print("Instance '%s': %s" % (iid, image))
    return 0

def _resolve_status_instance(args):
    """Pick the instance the user wants status for.

    Same dispatch as `canasta delete`: --id wins; if absent, look up
    by current working directory against the registry's `path` field.
    Returns (id, instance_dict) or (None, None) on no match.
    """
    inst_id = getattr(args, "id", None)
    conf_path = os.path.join(_helpers._get_config_dir(), "conf.json")
    instances = _helpers._read_registry(conf_path)
    if inst_id:
        return (inst_id, instances.get(inst_id))
    cwd = os.environ.get("CANASTA_HOST_PWD") or os.getcwd()
    for k, v in instances.items():
        if v.get("path") == cwd:
            return (k, v)
    return (None, None)


def _kubectl_section(host, ns, cmd_args, label):
    """Run a kubectl get (locally or via SSH) and return formatted output.

    Always runs against namespace `ns`. Returns a (label, body) tuple
    suitable for printing.
    """
    cmd = "kubectl get %s -n %s" % (cmd_args, ns)
    if _helpers._is_localhost(host):
        try:
            r = subprocess.run(
                cmd.split(), capture_output=True, text=True, timeout=15,
            )
            rc, out = r.returncode, r.stdout
        except (subprocess.TimeoutExpired, OSError):
            return (label, "(query failed)")
    else:
        rc, out = _helpers._ssh_run(host, cmd)
    if rc != 0:
        if "not found" in out.lower() or "no resources found" in out.lower():
            return (label, "(none)")
        return (label, "(query failed)")
    if not out.strip():
        return (label, "(none)")
    return (label, out.rstrip())

@register("status")
def cmd_status(args):
    inst_id, inst = _resolve_status_instance(args)
    if not inst:
        if getattr(args, "id", None):
            print(
                "Error: instance '%s' not found in the registry."
                % args.id, file=sys.stderr,
            )
        else:
            print(
                "Error: no instance found for the current directory; "
                "pass --id explicitly.", file=sys.stderr,
            )
        return 1

    orchestrator = inst.get("orchestrator", "compose")
    host = inst.get("host", "localhost")
    path = inst.get("path", "")

    # Header
    print("Instance:     %s" % inst_id)
    print("Host:         %s" % host)
    print("Orchestrator: %s" % orchestrator.upper())

    if orchestrator in ("kubernetes", "k8s"):
        running = _helpers._check_running_k8s(inst_id, host)
        print("Status:       %s" % ("RUNNING" if running else "STOPPED"))
        ns = "canasta-%s" % inst_id
        sections = [
            ("Pods", "pods -o wide"),
            ("Volumes", "pvc"),
            ("Services", "svc"),
            ("Ingress", "ingress"),
            ("Certificate", "certificate"),
        ]
        for label, cmd_args in sections:
            tag, body = _kubectl_section(host, ns, cmd_args, label)
            print()
            print("%s:" % tag)
            for line in body.splitlines():
                print("  %s" % line)
        return 0

    # Compose
    running = _helpers._check_running_compose(path, host)
    print("Status:       %s" % ("RUNNING" if running else "STOPPED"))
    if not path:
        print("\n(no path on file — cannot inspect containers)")
        return 0

    ps_cmd = "docker compose ps"
    if _helpers._is_localhost(host):
        try:
            r = subprocess.run(
                ps_cmd.split(),
                cwd=path, capture_output=True, text=True, timeout=15,
            )
            rc, out = r.returncode, r.stdout
        except (subprocess.TimeoutExpired, OSError):
            rc, out = 1, ""
    else:
        rc, out = _helpers._ssh_run(
            host, "cd %s && %s" % (_helpers._shell_quote(path), ps_cmd),
        )
    print()
    print("Containers:")
    if rc != 0 or not out.strip():
        print("  (query failed or no containers)")
    else:
        for line in out.rstrip().splitlines():
            print("  %s" % line)
    return 0
