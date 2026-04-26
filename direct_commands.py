"""Direct command implementations that bypass Ansible.

Commands registered here run as pure Python, avoiding the ~3-5s
overhead of ansible-playbook startup for simple operations.
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml


DIRECT_COMMANDS = {}


def register(command_name):
    """Decorator to register a direct command handler."""
    def decorator(func):
        DIRECT_COMMANDS[command_name] = func
        return func
    return decorator


FALLBACK = object()


def is_direct_command(command_name):
    return command_name in DIRECT_COMMANDS


def run_direct_command(command_name, args):
    handler = DIRECT_COMMANDS[command_name]
    return handler(args)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_config_dir():
    from canasta import get_config_dir
    return get_config_dir()


def _get_script_dir():
    from canasta import SCRIPT_DIR
    return SCRIPT_DIR


def _resolve_instance(args):
    """Resolve instance from --id flag or cwd. Returns (id, inst_dict) or exits."""
    from canasta import resolve_instance
    instance_id = getattr(args, "id", None)
    inst = resolve_instance(instance_id)
    return inst["id"], inst


def _read_env_content(path, host):
    """Read raw .env file content. Returns '' if missing or unreadable."""
    env_path = os.path.join(path, ".env")
    if _is_localhost(host):
        try:
            with open(env_path) as f:
                return f.read()
        except OSError:
            return ""
    rc, content = _ssh_run(host, "cat %s 2>/dev/null" % _shell_quote(env_path))
    return content if rc == 0 else ""


def _parse_env_entries(content):
    """Parse .env content into ordered (key, value, is_comment) tuples.

    Preserves comments and blank lines so a round-trip through
    _entries_to_content leaves untouched lines intact. Mirrors
    canasta_env.parse_env_file() in the Ansible module.
    """
    entries = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            entries.append((None, line, True))
            continue
        parts = stripped.split("=", 1)
        if len(parts) == 2:
            key = parts[0].strip()
            value = parts[1].strip()
            if len(value) >= 2 and (
                (value.startswith('"') and value.endswith('"'))
                or (value.startswith("'") and value.endswith("'"))
            ):
                value = value[1:-1]
            entries.append((key, value, False))
        else:
            entries.append((None, line, True))
    return entries


def _entries_to_content(entries):
    lines = []
    for key, value, is_comment in entries:
        if is_comment:
            lines.append(value)
        else:
            lines.append("%s=%s" % (key, value))
    return "\n".join(lines)


def _set_env_entry(entries, key, value):
    """Update first occurrence, drop duplicate lines for the same key,
    append if absent. Matches canasta_env.set_variable() in Ansible."""
    found = False
    new_entries = []
    for k, v, c in entries:
        if not c and k == key:
            if not found:
                new_entries.append((key, value, False))
                found = True
            # Drop subsequent duplicates
        else:
            new_entries.append((k, v, c))
    if not found:
        new_entries.append((key, value, False))
    return new_entries


def _write_env_content(path, host, content):
    """Write content to .env. Returns True on success.

    Remote writes pipe stdin through 'cat > file' over SSH — avoids
    escaping the entire file content into a command line.
    """
    env_path = os.path.join(path, ".env")
    if _is_localhost(host):
        try:
            with open(env_path, "w") as f:
                f.write(content)
            return True
        except OSError as e:
            print("Error writing %s: %s" % (env_path, e), file=sys.stderr)
            return False

    ssh_cmd = (
        ["ssh"] + _ssh_args()
        + [host, "cat > %s" % _shell_quote(env_path)]
    )
    try:
        result = subprocess.run(
            ssh_cmd, input=content, text=True,
            capture_output=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print("Error writing remote %s: %s" % (env_path, e), file=sys.stderr)
        return False
    if result.returncode != 0:
        print(
            "Error writing remote %s: %s"
            % (env_path, (result.stderr or "").strip()),
            file=sys.stderr,
        )
        return False
    return True


def _read_env_file(path, host):
    """Read and parse a .env file, returning a dict of key=value pairs."""
    content = _read_env_content(path, host)
    return {k: v for k, v, c in _parse_env_entries(content) if not c and k}


def _compose_file_args(path, host, devmode=False):
    """Build docker compose -f flags based on available files."""
    files = ["docker-compose.yml"]
    override = os.path.join(path, "docker-compose.override.yml")
    if _is_localhost(host):
        if os.path.isfile(override):
            files.append("docker-compose.override.yml")
    else:
        rc, _ = _ssh_run(host, "test -f %s" % _shell_quote(override))
        if rc == 0:
            files.append("docker-compose.override.yml")
    if devmode:
        files.append("docker-compose.dev.yml")
    result = []
    for f in files:
        result.extend(["-f", f])
    return result


def _run_compose(inst_id, inst, action_args):
    """Run a docker compose command for an instance. Returns exit code."""
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    devmode = inst.get("devMode", False)
    file_args = _compose_file_args(path, host, devmode)

    if _is_localhost(host):
        cmd = ["docker", "compose"] + file_args + action_args
        try:
            result = subprocess.run(cmd, cwd=path, timeout=120)
            return result.returncode
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1
    else:
        file_str = " ".join(file_args)
        action_str = " ".join(action_args)
        rc, stdout = _ssh_run(
            host,
            "cd %s && docker compose %s %s" % (
                _shell_quote(path), file_str, action_str,
            ),
        )
        if stdout.strip():
            print(stdout.strip())
        return rc


# Profiles that Canasta derives from CANASTA_ENABLE_* feature flags.
# (profile_name, flag_name, default_when_flag_unset)
# Matches roles/orchestrator/tasks/sync_compose_profiles.yml.
_MANAGED_PROFILES = [
    ("observable", "CANASTA_ENABLE_OBSERVABILITY", "false"),
    ("elasticsearch", "CANASTA_ENABLE_ELASTICSEARCH", "false"),
    ("varnish", "CANASTA_ENABLE_VARNISH", "true"),
]


def _sync_compose_profiles(inst):
    """Align COMPOSE_PROFILES in .env with the CANASTA_ENABLE_* feature flags.

    Called before 'docker compose up' so hand-edited .env files, gitops
    pulls, or any other drift between the managed flags and
    COMPOSE_PROFILES gets reconciled before compose is invoked. Mirrors
    roles/orchestrator/tasks/sync_compose_profiles.yml — canasta config
    set has its own copy that fires on the relevant keys.
    """
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    content = _read_env_content(path, host)
    if not content:
        return  # No .env to sync

    entries = _parse_env_entries(content)
    env = {k: v for k, v, c in entries if not c and k}
    managed_names = {p for p, _flag, _default in _MANAGED_PROFILES}

    current_raw = env.get("COMPOSE_PROFILES", "")
    current = [p.strip() for p in current_raw.split(",") if p.strip()]

    desired = [p for p in current if p not in managed_names]
    for profile, flag, default in _MANAGED_PROFILES:
        if env.get(flag, default).strip().lower() == "true":
            desired.append(profile)

    if sorted(desired) == sorted(current):
        return  # No change needed

    new_entries = _set_env_entry(
        entries, "COMPOSE_PROFILES", ",".join(desired),
    )
    _write_env_content(path, host, _entries_to_content(new_entries))


def _dump_compose_failure(inst):
    """Print docker compose ps + logs to stderr after a failed 'up'.

    Matches the diagnostic block in roles/orchestrator/tasks/start.yml:
    the caller usually rolls back (docker compose down) on failure,
    which wipes the containers — so capturing ps/logs here, right
    after the failed 'up' and before the rollback, is the only
    reliable way to see why a container was unhealthy.
    """
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    devmode = inst.get("devMode", False)
    file_args = _compose_file_args(path, host, devmode)

    steps = [
        (["ps", "-a"], "docker compose ps -a"),
        (["logs", "--tail=200", "--no-color"], "docker compose logs (last 200)"),
    ]
    for action, label in steps:
        if _is_localhost(host):
            try:
                result = subprocess.run(
                    ["docker", "compose"] + file_args + action,
                    cwd=path, capture_output=True, text=True, timeout=30,
                )
                output = result.stdout or result.stderr
            except (subprocess.TimeoutExpired, OSError) as e:
                output = "(failed to capture: %s)" % e
        else:
            action_str = " ".join(action)
            file_str = " ".join(file_args)
            _rc, output = _ssh_run(
                host,
                "cd %s && docker compose %s %s" % (
                    _shell_quote(path), file_str, action_str,
                ),
            )
        print("--- %s ---" % label, file=sys.stderr)
        print(output.strip() if output else "(empty)", file=sys.stderr)


def _k8s_get_pod(namespace, component="web"):
    """Get the name of a running pod by component label."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace,
             "-l", "app.kubernetes.io/component=%s" % component,
             "--field-selector=status.phase=Running",
             "-o", "jsonpath={.items[0].metadata.name}"],
            capture_output=True, text=True, timeout=10,
        )
        pod = result.stdout.strip()
        return pod if result.returncode == 0 and pod else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _exec_in_container(inst_id, inst, command, service="web"):
    """Execute a command inside a container/pod. Returns (rc, stdout)."""
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    orchestrator = inst.get("orchestrator", "compose")

    if orchestrator in ("kubernetes", "k8s"):
        ns = _k8s_namespace(inst_id)
        pod = _k8s_get_pod(ns, service)
        if not pod:
            return 1, ""
        try:
            result = subprocess.run(
                ["kubectl", "exec", pod, "-n", ns, "--",
                 "/bin/bash", "-c", command],
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode, result.stdout
        except (subprocess.TimeoutExpired, OSError):
            return 1, ""

    if _is_localhost(host):
        try:
            result = subprocess.run(
                ["docker", "compose", "exec", "-T", service,
                 "/bin/bash", "-c", command],
                cwd=path, capture_output=True, text=True, timeout=30,
            )
            return result.returncode, result.stdout
        except (subprocess.TimeoutExpired, OSError):
            return 1, ""

    rc, stdout = _ssh_run(
        host,
        "cd %s && docker compose exec -T %s /bin/bash -c %s" % (
            _shell_quote(path), service, _shell_quote(command),
        ),
    )
    return rc, stdout


def _read_registry(conf_path):
    if not os.path.isfile(conf_path):
        return {}
    with open(conf_path) as f:
        data = json.load(f)
    instances = data.get("Instances", data.get("Installations", {}))
    return instances


def _write_registry(conf_path, instances):
    if os.path.isfile(conf_path):
        with open(conf_path) as f:
            data = json.load(f)
    else:
        data = {}
    data["Instances"] = instances
    os.makedirs(os.path.dirname(conf_path), exist_ok=True)
    with open(conf_path, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")


def _host_matches(stored, target):
    bare = stored.split("@", 1)[-1] if "@" in stored else stored
    return bare == target or stored == target


def _filter_by_host(instances, host):
    if not host:
        return instances
    return {
        k: v for k, v in instances.items()
        if _host_matches(v.get("host", "localhost"), host)
    }


def _ssh_args():
    extra = os.environ.get(
        "ANSIBLE_SSH_ARGS",
        "-o StrictHostKeyChecking=accept-new",
    )
    return extra.split() if extra else []


def _ssh_run(host, cmd):
    full_cmd = ["ssh"] + _ssh_args() + [host, cmd]
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 and result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired:
        print("Error: SSH connection to %s timed out" % host, file=sys.stderr)
        return 1, ""
    except OSError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1, ""


def _is_localhost(host):
    return host in ("localhost", "", None)


def _check_dir_exists(path, host):
    if _is_localhost(host):
        return os.path.isdir(path)
    rc, _ = _ssh_run(host, "test -d %s" % _shell_quote(path))
    return rc == 0


def _shell_quote(s):
    """Shell-quote a string for use in SSH commands."""
    return "'" + s.replace("'", "'\\''") + "'"


def _read_wikis(path, host):
    wikis_path = os.path.join(path, "config", "wikis.yaml")
    try:
        if _is_localhost(host):
            with open(wikis_path) as f:
                data = yaml.safe_load(f)
        else:
            rc, stdout = _ssh_run(
                host, "cat %s" % _shell_quote(wikis_path),
            )
            if rc != 0 or not stdout.strip():
                return []
            data = yaml.safe_load(stdout)
        return data.get("wikis", []) if data else []
    except (OSError, yaml.YAMLError):
        return []


def _check_running(instance_id, path, orchestrator, host):
    if orchestrator in ("kubernetes", "k8s"):
        return _check_running_k8s(instance_id, host)
    return _check_running_compose(path, host)


def _check_running_compose(path, host):
    if _is_localhost(host):
        try:
            result = subprocess.run(
                ["docker", "compose", "ps", "-q", "web"],
                cwd=path, capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0 and result.stdout.strip() != ""
        except (subprocess.TimeoutExpired, OSError):
            return False
    else:
        rc, stdout = _ssh_run(
            host,
            "cd %s && docker compose ps -q web" % _shell_quote(path),
        )
        return rc == 0 and stdout.strip() != ""


def _check_running_k8s(instance_id, host):
    """True if the instance's web deployment has ≥1 ready replica.

    For remote hosts, SSH and run kubectl on the host — its kubeconfig
    points at the cluster it's part of. Running kubectl on the laptop
    (the previous behavior) only worked when the laptop happened to
    have a kubeconfig for that cluster, otherwise the call hit
    whatever the laptop's KUBECONFIG pointed at (Docker Desktop, an
    unrelated cluster, ...) and reported STOPPED for instances that
    were running fine.
    """
    if _is_localhost(host):
        try:
            result = subprocess.run(
                [
                    "kubectl", "get",
                    "deployment/canasta-%s-web" % instance_id,
                    "-n", "canasta-%s" % instance_id,
                    "-o", "jsonpath={.status.readyReplicas}",
                ],
                capture_output=True, text=True, timeout=10,
            )
            return (
                result.returncode == 0
                and result.stdout.strip() not in ("", "0")
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
    cmd = (
        "kubectl get deployment/canasta-%s-web "
        "-n canasta-%s -o jsonpath='{.status.readyReplicas}'"
        % (instance_id, instance_id)
    )
    rc, stdout = _ssh_run(host, cmd)
    return rc == 0 and stdout.strip() not in ("", "0")


_SENTINEL = "---CANASTA_DELIM---"


def _gather_instance_info(inst_id, inst):
    """Gather dir existence, wikis, and running status in one operation.

    For remote hosts this batches all checks into a single SSH call.
    Returns a details dict ready for _print_table.
    """
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    orchestrator = inst.get("orchestrator", "compose")
    qpath = _shell_quote(path)

    if _is_localhost(host):
        return _gather_local(inst_id, path, orchestrator, host)

    if orchestrator in ("kubernetes", "k8s"):
        return _gather_k8s(inst_id, path, host)

    script = (
        "test -d %(p)s && echo DIR_OK || echo DIR_MISSING; "
        "echo '%(d)s'; "
        "cat %(p)s/config/wikis.yaml 2>/dev/null || echo WIKIS_MISSING; "
        "echo '%(d)s'; "
        "cd %(p)s && docker compose ps -q web 2>/dev/null || true"
    ) % {"p": qpath, "d": _SENTINEL}

    rc, stdout = _ssh_run(host, script)
    if rc != 0 and not stdout.strip():
        return _make_detail(inst_id, host, path, orchestrator, "NOT FOUND", [])

    parts = stdout.split(_SENTINEL + "\n")
    dir_ok = parts[0].strip() == "DIR_OK" if len(parts) > 0 else False
    wikis_raw = parts[1] if len(parts) > 1 else ""
    running_raw = parts[2].strip() if len(parts) > 2 else ""

    wikis = []
    if dir_ok and wikis_raw.strip() != "WIKIS_MISSING":
        try:
            parsed = yaml.safe_load(wikis_raw)
            wikis = parsed.get("wikis", []) if parsed else []
        except yaml.YAMLError:
            pass

    if not dir_ok:
        status = "NOT FOUND"
    elif running_raw:
        status = "RUNNING"
    else:
        status = "STOPPED"

    return _make_detail(inst_id, host, path, orchestrator, status, wikis)


def _gather_local(inst_id, path, orchestrator, host):
    dir_exists = os.path.isdir(path)
    wikis = _read_wikis(path, host) if dir_exists else []

    if not dir_exists:
        status = "NOT FOUND"
    elif _check_running(inst_id, path, orchestrator, host):
        status = "RUNNING"
    else:
        status = "STOPPED"

    return _make_detail(inst_id, host, path, orchestrator, status, wikis)


def _gather_k8s(inst_id, path, host):
    dir_exists = _check_dir_exists(path, host)
    wikis = _read_wikis(path, host) if dir_exists else []

    if not dir_exists:
        status = "NOT FOUND"
    elif _check_running_k8s(inst_id, host):
        status = "RUNNING"
    else:
        status = "STOPPED"

    return _make_detail(inst_id, host, path, "kubernetes", status, wikis)


def _make_detail(inst_id, host, path, orchestrator, status, wikis):
    return {
        "id": inst_id,
        "host": host,
        "path": path,
        "orchestrator": orchestrator.upper(),
        "status": status,
        "wikis": wikis,
    }


def _print_table(details):
    host_lengths = [len(d["host"]) for d in details] + [16]
    hw = max(host_lengths) + 2

    print("%-16s%-*s%s" % ("Canasta ID", hw, "Host", "Instance Path"))
    print("  %-14s%s" % ("Orchestrator", "Status"))
    print("  %-14s%s" % ("Wiki ID", "Wiki URL"))
    print("\u2500" * (16 + hw + 20))

    for i, d in enumerate(details):
        print(
            "%-16s%-*s%s" % (d["id"], hw, d["host"], d["path"])
        )
        print("  %-14s%s" % (d["orchestrator"], d["status"]))
        if d["wikis"]:
            for w in d["wikis"]:
                url = w.get("url", "")
                if "/" not in url:
                    url = url + "/"
                print("  %-14s%s" % (w.get("id", "?"), url))
        else:
            print("  (no wikis)")
        if i < len(details) - 1:
            print()


# ---------------------------------------------------------------------------
# canasta list
# ---------------------------------------------------------------------------

def _gather_all_instances(instances):
    """Gather info for all instances in parallel."""
    if not instances:
        return []

    ordered_ids = list(instances.keys())
    results = {}

    with ThreadPoolExecutor(max_workers=len(instances)) as pool:
        futures = {
            pool.submit(_gather_instance_info, iid, inst): iid
            for iid, inst in instances.items()
        }
        for future in as_completed(futures):
            iid = futures[future]
            try:
                results[iid] = future.result()
            except Exception:
                inst = instances[iid]
                results[iid] = _make_detail(
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

    if _is_localhost(host):
        return "exists" if os.path.isdir(path) else "missing"

    # OpenSSH returns rc=255 specifically for transport-layer failures.
    # Any other non-zero rc means the remote command ran and failed on
    # its own terms — for 'test -d' that means the directory really is
    # missing on the remote host.
    rc, _ = _ssh_run(host, "test -d %s" % _shell_quote(path))
    if rc == 0:
        return "exists"
    if rc == 255:
        return "unreachable"
    return "missing"


@register("list")
def cmd_list(args):
    config_dir = _get_config_dir()
    conf_path = os.path.join(config_dir, "conf.json")

    if getattr(args, "cleanup", False):
        instances = _read_registry(conf_path)
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
                _write_registry(conf_path, instances)
                print("Removed stale entries: %s" % ", ".join(to_remove))
            if unreachable and not force:
                plural = "y" if len(unreachable) == 1 else "ies"
                print(
                    "Kept %d unreachable entr%s (pass --force to remove): %s"
                    % (len(unreachable), plural, ", ".join(unreachable))
                )

    instances = _read_registry(conf_path)
    host_filter = getattr(args, "host", None)
    instances = _filter_by_host(instances, host_filter)

    if not instances:
        print("No Canasta instances.")
        return 0

    details = _gather_all_instances(instances)

    _print_table(details)
    return 0


# ---------------------------------------------------------------------------
# canasta version
# ---------------------------------------------------------------------------

def _read_instance_image(inst_id, inst):
    """Return 'CANASTA_IMAGE (running: X)' for a single instance entry.

    'running' comes from /tmp/canasta-version inside the web container;
    absent when the container isn't up or the command fails for any
    reason. Never raises.
    """
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    env_vars = _read_env_file(path, host)
    image = env_vars.get("CANASTA_IMAGE", "(unset)")

    # Running Canasta version — line 2 of /tmp/canasta-version inside
    # the web container. Best effort; fall back silently.
    running = "(not running)"
    compose_cmd = (
        "cd %s && docker compose exec -T web sh -c "
        "\"cat /tmp/canasta-version 2>/dev/null | sed -n '2p'\" 2>/dev/null"
        % _shell_quote(path)
    )
    if _is_localhost(host):
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
        rc, stdout = _ssh_run(host, compose_cmd)
        if rc == 0 and stdout.strip():
            running = stdout.strip()

    return image, running


@register("version")
def cmd_version(args):
    script_dir = _get_script_dir()

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
        inst_id, inst = _resolve_instance(args)
        image, running = _read_instance_image(inst_id, inst)
        print("Instance '%s': %s (running: %s)" % (inst_id, image, running))
        return 0

    # No -i: try cwd resolution. If inside an instance directory,
    # give the same full-fidelity report as -i.
    config_dir = _get_config_dir()
    conf_path = os.path.join(config_dir, "conf.json")
    instances = _read_registry(conf_path)
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
    instances = _filter_by_host(instances, host_filter)
    if not instances:
        print("No instances registered.")
        return 0
    for iid in sorted(instances.keys()):
        host = instances[iid].get("host") or "localhost"
        path = instances[iid].get("path", "")
        env_vars = _read_env_file(path, host)
        image = env_vars.get("CANASTA_IMAGE", "(unset)")
        print("Instance '%s': %s" % (iid, image))
    return 0


# ---------------------------------------------------------------------------
# canasta config get
# ---------------------------------------------------------------------------

@register("config_get")
def cmd_config_get(args):
    inst_id, inst = _resolve_instance(args)
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")

    env_vars = _read_env_file(path, host)
    if not env_vars:
        print("No configuration found.", file=sys.stderr)
        return 1

    # Positional 'keys' comes from argparse as a list when run through
    # canasta.py. For each explicitly requested key, print KEY=value or
    # a not-found message. With no keys, dump every setting, sorted.
    keys = getattr(args, "keys", None) or []
    if keys:
        for k in keys:
            if k in env_vars:
                print("%s=%s" % (k, env_vars[k]))
            else:
                print("Key '%s' not found." % k)
        return 0

    for k in sorted(env_vars.keys()):
        print("%s=%s" % (k, env_vars[k]))
    return 0


# ---------------------------------------------------------------------------
# canasta start / stop / restart
# ---------------------------------------------------------------------------

def _k8s_namespace(instance_id):
    return "canasta-%s" % instance_id


def _run_kubectl(kubectl_args, timeout=30):
    """Run a kubectl command. Returns exit code."""
    cmd = ["kubectl"] + kubectl_args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.returncode != 0 and result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    except (subprocess.TimeoutExpired, OSError) as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1


def _k8s_stop(instance_id):
    """Stop a K8s instance: suspend Argo CD sync, scale everything to 0."""
    ns = _k8s_namespace(instance_id)

    result = subprocess.run(
        ["kubectl", "get", "application", "canasta-%s" % instance_id,
         "-n", "argocd", "-o", "jsonpath={.metadata.name}"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        _run_kubectl([
            "patch", "application", "canasta-%s" % instance_id,
            "-n", "argocd", "--type", "merge",
            "-p", '{"spec":{"syncPolicy":null}}',
        ])

    _run_kubectl(["scale", "deployment", "--all", "--replicas=0", "-n", ns])

    # External-DB instances with Elasticsearch disabled have no
    # StatefulSets; 'kubectl scale --all' errors with "no objects
    # passed to scale". Check first and skip if none exist.
    sts_check = subprocess.run(
        ["kubectl", "get", "statefulset", "-n", ns, "-o", "name"],
        capture_output=True, text=True, timeout=10,
    )
    if sts_check.returncode == 0 and sts_check.stdout.strip():
        _run_kubectl(["scale", "statefulset", "--all", "--replicas=0", "-n", ns])
    return 0


@register("start")
def cmd_start(args):
    inst_id, inst = _resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        # K8s start requires chart copy + helm deploy + config sync;
        # these are multi-step controller-to-remote operations that
        # need Ansible.
        return FALLBACK
    _sync_compose_profiles(inst)
    rc = _run_compose(inst_id, inst, ["up", "-d"])
    if rc != 0:
        _dump_compose_failure(inst)
    return rc


@register("stop")
def cmd_stop(args):
    inst_id, inst = _resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        return _k8s_stop(inst_id)
    return _run_compose(inst_id, inst, ["down"])


@register("restart")
def cmd_restart(args):
    inst_id, inst = _resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        # K8s restart needs Ansible for the start half (helm deploy).
        return FALLBACK
    rc = _run_compose(inst_id, inst, ["down"])
    if rc != 0:
        return rc
    _sync_compose_profiles(inst)
    rc = _run_compose(inst_id, inst, ["up", "-d"])
    if rc != 0:
        _dump_compose_failure(inst)
    return rc


# ---------------------------------------------------------------------------
# canasta host list / add / remove
# ---------------------------------------------------------------------------

def _hosts_yml_path():
    return os.path.join(_get_config_dir(), "hosts.yml")


def _read_hosts_yml():
    path = _hosts_yml_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data if data else None
    except (OSError, yaml.YAMLError):
        return None


def _write_hosts_yml(data):
    path = _hosts_yml_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, indent=2)


@register("host_list")
def cmd_host_list(args):
    data = _read_hosts_yml()
    if data is None:
        print("No hosts configured (no file at %s)." % _hosts_yml_path())
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

    data = _read_hosts_yml()
    if data is None:
        data = {"all": {"hosts": {}}}

    entry = {"ansible_host": ssh_host}
    if ssh_user:
        entry["ansible_user"] = ssh_user
    if python_path:
        entry["ansible_python_interpreter"] = python_path

    data.setdefault("all", {}).setdefault("hosts", {})[host_name] = entry
    _write_hosts_yml(data)
    print("Host '%s' saved to %s" % (host_name, _hosts_yml_path()))
    return 0


@register("host_remove")
def cmd_host_remove(args):
    host_name = getattr(args, "host_name", "")
    data = _read_hosts_yml()

    if data is None:
        print("No hosts.yml found at %s" % _hosts_yml_path(), file=sys.stderr)
        return 1

    hosts = data.get("all", {}).get("hosts", {})
    if host_name not in hosts:
        print("Host '%s' not found in %s" % (host_name, _hosts_yml_path()), file=sys.stderr)
        return 1

    del hosts[host_name]
    _write_hosts_yml(data)
    print("Host '%s' removed from %s" % (host_name, _hosts_yml_path()))
    return 0


# ---------------------------------------------------------------------------
# canasta gitops status
# ---------------------------------------------------------------------------

def _gitops_status_script(path):
    """Build a batched shell script that gathers all gitops status info."""
    d = _SENTINEL
    qp = _shell_quote(path)
    return (
        "cd %(p)s; "
        "cat .gitops-host 2>/dev/null || echo MISSING; "
        "echo '%(d)s'; "
        "cat hosts/hosts.yaml 2>/dev/null || echo MISSING; "
        "echo '%(d)s'; "
        "git rev-parse --short HEAD 2>/dev/null || echo none; "
        "echo '%(d)s'; "
        "cat .gitops-applied 2>/dev/null || echo none; "
        "echo '%(d)s'; "
        "git diff --cached --name-only 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff --name-only 2>/dev/null; "
        "echo '%(d)s'; "
        "git fetch 2>/dev/null; "
        "git rev-list --left-right --count HEAD...@{upstream} 2>/dev/null || echo '0\t0'"
    ) % {"p": qp, "d": d}


def _parse_gitops_status(stdout, instance_id):
    """Parse the batched gitops status output into a formatted string."""
    parts = stdout.split(_SENTINEL + "\n")

    hostname = parts[0].strip() if len(parts) > 0 else "unknown"
    if hostname == "MISSING":
        hostname = "unknown"

    hosts_yaml_raw = parts[1].strip() if len(parts) > 1 else ""
    role = "unknown"
    pull_requests = False
    if hosts_yaml_raw and hosts_yaml_raw != "MISSING":
        try:
            parsed = yaml.safe_load(hosts_yaml_raw)
            if parsed and "hosts" in parsed and parsed["hosts"]:
                role = parsed["hosts"][0].get("role", "unknown")
                pull_requests = parsed["hosts"][0].get("pull_requests", False)
        except yaml.YAMLError:
            pass

    commit = parts[2].strip() if len(parts) > 2 else "none"
    applied = parts[3].strip() if len(parts) > 3 else "none"
    staged_raw = parts[4].strip() if len(parts) > 4 else ""
    unstaged_raw = parts[5].strip() if len(parts) > 5 else ""
    revcount_raw = parts[6].strip() if len(parts) > 6 else "0\t0"

    staged = staged_raw.split("\n") if staged_raw else []
    unstaged = unstaged_raw.split("\n") if unstaged_raw else []

    try:
        revcount_parts = revcount_raw.split("\t")
        ahead = int(revcount_parts[0])
        behind = int(revcount_parts[1]) if len(revcount_parts) > 1 else 0
    except (ValueError, IndexError):
        ahead, behind = 0, 0

    lines = [
        "Host:           %s" % hostname,
        "Role:           %s" % role,
        "Canasta ID:     %s" % instance_id,
        "Pull requests:  %s" % str(pull_requests),
        "Current commit: %s" % commit,
        "Last applied:   %s" % (applied if applied != "none" else ""),
        "",
    ]

    if staged:
        lines.append("Staged for push (%d files):" % len(staged))
        for f in staged:
            lines.append("  %s" % f)
        lines.append("")

    if unstaged:
        lines.append("Unstaged changes (%d files):" % len(unstaged))
        for f in unstaged:
            lines.append("  %s" % f)
        lines.append("")

    if not staged and not unstaged:
        lines.append("No changes.")
        lines.append("")

    if ahead > 0:
        lines.append("Ahead of remote by %d commit(s)." % ahead)
    elif behind > 0:
        lines.append("Behind remote by %d commit(s)." % behind)
    else:
        lines.append("Up to date with remote.")

    return "\n".join(lines)


def _gitops_argocd_status(instance_id):
    """Query Argo CD Application for this instance; return parsed status.

    Returns a tuple (sync_status, health, last_sync, revision). Falls
    back to 'Not registered' / 'N/A' sentinels when Argo CD isn't
    installed or no Application exists — matches the Ansible path's
    behavior for K8s instances without Argo CD.
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", "application", "canasta-%s" % instance_id,
             "-n", "argocd", "-o", "json"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ("Not registered", "N/A", "never", "unknown")
    if result.returncode != 0:
        return ("Not registered", "N/A", "never", "unknown")
    try:
        app = json.loads(result.stdout)
    except ValueError:
        return ("Unknown", "Unknown", "never", "unknown")
    status = app.get("status") or {}
    sync = status.get("sync") or {}
    health = status.get("health") or {}
    op = status.get("operationState") or {}
    revision = sync.get("revision") or "unknown"
    return (
        sync.get("status") or "Unknown",
        health.get("status") or "Unknown",
        op.get("finishedAt") or "never",
        revision[:7],
    )


def _parse_gitops_status_k8s(stdout, instance_id, argocd):
    """Format K8s gitops status. Matches roles/gitops/tasks/status_kubernetes.yml."""
    parts = stdout.split(_SENTINEL + "\n")
    hostname = parts[0].strip() if len(parts) > 0 else "unknown"
    if hostname == "MISSING":
        hostname = "unknown"
    commit = parts[2].strip() if len(parts) > 2 else "none"
    revcount_raw = parts[6].strip() if len(parts) > 6 else "0\t0"
    try:
        revcount_parts = revcount_raw.split("\t")
        ahead = int(revcount_parts[0])
        behind = int(revcount_parts[1]) if len(revcount_parts) > 1 else 0
    except (ValueError, IndexError):
        ahead, behind = 0, 0

    sync_status, health, last_sync, revision = argocd

    lines = [
        "Host:             %s" % hostname,
        "Canasta ID:       %s" % instance_id,
        "Current commit:   %s" % commit,
        "Ahead of remote:  %d" % ahead,
        "Behind remote:    %d" % behind,
        "",
        "Argo CD:",
        "  Sync status:    %s" % sync_status,
        "  Health status:  %s" % health,
        "  Last sync:      %s" % last_sync,
        "  Applied rev:    %s" % revision,
    ]
    if sync_status == "OutOfSync":
        lines.append("")
        lines.append("Note: Run 'canasta gitops sync' to apply pending changes immediately.")
    return "\n".join(lines)


@register("gitops_status")
def cmd_gitops_status(args):
    inst_id, inst = _resolve_instance(args)
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    orchestrator = inst.get("orchestrator", "compose")

    script = _gitops_status_script(path)

    if _is_localhost(host):
        try:
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30,
            )
            stdout = result.stdout
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1
    else:
        rc, stdout = _ssh_run(host, script)
        if rc != 0 and not stdout.strip():
            print("Error: failed to connect to %s" % host, file=sys.stderr)
            return 1

    if orchestrator in ("kubernetes", "k8s"):
        argocd = _gitops_argocd_status(inst_id)
        print(_parse_gitops_status_k8s(stdout, inst_id, argocd))
    else:
        print(_parse_gitops_status(stdout, inst_id))
    return 0


# ---------------------------------------------------------------------------
# canasta extension list / skin list
# ---------------------------------------------------------------------------

def _list_items(args, item_type, item_dir):
    inst_id, inst = _resolve_instance(args)
    command = (
        "cd /var/www/mediawiki/w/%s "
        "&& find -L * -maxdepth 0 -type d 2>/dev/null "
        "|| true" % item_dir
    )
    rc, stdout = _exec_in_container(inst_id, inst, command)
    if rc != 0:
        print("Error: could not list %s." % item_type, file=sys.stderr)
        return 1
    print("Available Canasta %s:" % item_type)
    print(stdout.strip())
    return 0


@register("extension_list")
def cmd_extension_list(args):
    return _list_items(args, "extensions", "extensions")


@register("skin_list")
def cmd_skin_list(args):
    return _list_items(args, "skins", "skins")


# ---------------------------------------------------------------------------
# canasta gitops diff
# ---------------------------------------------------------------------------

def _gitops_diff_script(path):
    d = _SENTINEL
    qp = _shell_quote(path)
    return (
        "cd %(p)s; "
        "git diff --name-only 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff --name-only HEAD @{upstream} 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff --name-only @{upstream} HEAD 2>/dev/null; "
        "echo '%(d)s'; "
        "git submodule status 2>/dev/null"
    ) % {"p": qp, "d": d}


def _parse_gitops_diff(stdout):
    import re
    parts = stdout.split(_SENTINEL + "\n")

    uncommitted = parts[0].strip() if len(parts) > 0 else ""
    local = parts[1].strip() if len(parts) > 1 else ""
    remote = parts[2].strip() if len(parts) > 2 else ""
    submodules_raw = parts[3].strip() if len(parts) > 3 else ""

    uc_files = sorted(uncommitted.split("\n")) if uncommitted else []
    lc_files = sorted(local.split("\n")) if local else []
    rc_files = sorted(remote.split("\n")) if remote else []

    lines = ["Uncommitted changes: %d file(s)" % len(uc_files)]
    for f in uc_files:
        lines.append("  %s" % f)
    lines.append("")

    lines.append("Local changes (not yet pushed): %d file(s)" % len(lc_files))
    for f in lc_files:
        lines.append("  %s" % f)
    lines.append("")

    lines.append("Remote changes (would be applied on pull): %d file(s)" % len(rc_files))
    for f in rc_files:
        lines.append("  %s" % f)
    lines.append("")

    subs = [
        s.split()[1] for s in submodules_raw.split("\n")
        if s.startswith("+") and len(s.split()) > 1
    ]
    if subs:
        lines.append("Submodules that would be updated:")
        for s in subs:
            lines.append("  %s" % s)
        lines.append("")

    all_changed = lc_files + rc_files
    needs_restart = any(
        re.search(r'\.(env|yml|yaml)$', f) for f in all_changed
    )
    needs_update = any(f.endswith(".php") for f in all_changed)
    if needs_restart:
        lines.append("A restart would be needed after pulling.")
    if needs_update:
        lines.append("A maintenance update may be needed after pulling.")

    return "\n".join(lines)


@register("gitops_diff")
def cmd_gitops_diff(args):
    inst_id, inst = _resolve_instance(args)
    orchestrator = inst.get("orchestrator", "compose")

    if orchestrator in ("kubernetes", "k8s"):
        return FALLBACK

    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    script = _gitops_diff_script(path)

    if _is_localhost(host):
        try:
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30,
            )
            stdout = result.stdout
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1
    else:
        rc, stdout = _ssh_run(host, script)
        if rc != 0 and not stdout.strip():
            print("Error: failed to connect to %s" % host, file=sys.stderr)
            return 1

    print(_parse_gitops_diff(stdout))
    return 0


# ---------------------------------------------------------------------------
# canasta backup list
# ---------------------------------------------------------------------------

@register("backup_list")
def cmd_backup_list(args):
    inst_id, inst = _resolve_instance(args)
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    orchestrator = inst.get("orchestrator", "compose")

    if orchestrator in ("kubernetes", "k8s"):
        return FALLBACK

    bvol = "canasta-backup-%s" % os.path.basename(path)
    env_vars = _read_env_file(path, host)
    local_repo = env_vars.get("RESTIC_REPOSITORY", "")
    local_mount = ""
    if local_repo.startswith("/"):
        qrepo = _shell_quote(local_repo)
        local_mount = "-v %s:%s" % (qrepo, qrepo)

    qpath = _shell_quote(path)
    cmd = (
        "docker volume create %(vol)s >/dev/null 2>&1; "
        "docker run --rm -i "
        "--env-file %(path)s/.env "
        "-v %(vol)s:/currentsnapshot "
        "%(local_mount)s "
        "restic/restic "
        "--cache-dir /tmp/restic-cache "
        "snapshots"
    ) % {"vol": _shell_quote(bvol), "path": qpath, "local_mount": local_mount}

    if _is_localhost(host):
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=60,
            )
            if result.stdout.strip():
                print(result.stdout.strip())
            return result.returncode
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1

    rc, stdout = _ssh_run(host, cmd)
    if stdout.strip():
        print(stdout.strip())
    return rc


# ---------------------------------------------------------------------------
# canasta doctor
# ---------------------------------------------------------------------------

_DOCTOR_SCRIPT = r"""
D='%(delim)s'
python3 --version 2>&1 || echo MISSING; echo "$D"
docker --version 2>&1 || echo MISSING; echo "$D"
docker compose version 2>&1 || echo MISSING; echo "$D"
docker info >/dev/null 2>&1 && echo OK || echo NOT_RUNNING; echo "$D"
id -nG 2>/dev/null || echo ""; echo "$D"
kubectl version --client --output=yaml >/dev/null 2>&1 && echo OK || echo MISSING; echo "$D"
helm version --short 2>/dev/null || echo MISSING; echo "$D"
k3s --version 2>/dev/null || echo MISSING; echo "$D"
kubectl cluster-info >/dev/null 2>&1 && echo REACHABLE || echo UNREACHABLE; echo "$D"
kubectl get deployment argocd-server -n argocd >/dev/null 2>&1 && echo INSTALLED || echo MISSING; echo "$D"
git --version 2>/dev/null || echo MISSING; echo "$D"
git-crypt --version 2>/dev/null && echo OK || echo MISSING; echo "$D"
python3 -c "import os; mem=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')//(1024**3); print(str(mem)+' GB')" 2>/dev/null || echo unknown; echo "$D"
df -h / | awk 'NR==2{print $4}' 2>/dev/null || echo unknown
"""


def _parse_doctor(stdout, hostname):
    d = _SENTINEL
    parts = stdout.split(d + "\n")

    def p(i):
        return parts[i].strip() if i < len(parts) else "unknown"

    python = p(0)
    docker = p(1)
    compose = p(2)
    daemon = p(3)
    groups = p(4)
    kubectl = p(5)
    helm = p(6)
    k3s = p(7)
    cluster = p(8)
    argocd = p(9)
    git = p(10)
    gitcrypt = p(11)
    memory = p(12)
    disk = p(13) if len(parts) > 13 else "unknown"

    lines = [
        "Canasta Dependency Check (%s)" % hostname,
        "=" * 52,
        "",
        "Core (required):",
    ]
    lines.append("  Python 3:        %s" % (
        "OK (%s)" % python if "Python" in python else "MISSING"))
    lines.append("  Docker:          %s" % (
        "OK (%s)" % docker if "Docker" in docker else "MISSING"))
    lines.append("  Docker Compose:  %s" % (
        "OK (%s)" % compose if "Docker Compose" in compose else "MISSING"))
    lines.append("  Docker daemon:   %s" % (
        "OK (running)" if daemon == "OK" else "NOT RUNNING"))

    lines.append("")
    lines.append("Kubernetes (optional):")
    lines.append("  kubectl:         %s" % ("OK" if kubectl.strip() == "OK" else "not installed"))
    lines.append("  Helm:            %s" % (
        "OK (%s)" % helm if helm != "MISSING" else "not installed"))
    lines.append("  k3s:             %s" % (
        "OK" if k3s != "MISSING" else "not installed"))
    lines.append("  Cluster:         %s" % (
        "reachable" if cluster.strip() == "REACHABLE" else "not reachable"))
    lines.append("  Argo CD:         %s" % (
        "installed" if argocd.strip() == "INSTALLED" else "not found"))

    lines.append("")
    lines.append("GitOps (optional):")
    lines.append("  git:             %s" % (
        "OK (%s)" % git if "git version" in git else "not installed"))
    lines.append("  git-crypt:       %s" % (
        "OK" if gitcrypt == "OK" else "not installed"))

    lines.append("")
    lines.append("System:")
    lines.append("  Memory:          %s" % memory)
    lines.append("  Disk (/ avail):  %s" % disk)
    www_member = "www-data" in groups.split()
    lines.append("  www-data group:  %s" % (
        "OK (member)" if www_member else "NOT A MEMBER"))

    return "\n".join(lines)


@register("doctor")
def cmd_doctor(args):
    host = getattr(args, "host", None)
    script = _DOCTOR_SCRIPT % {"delim": _SENTINEL}

    if not host or host == "localhost":
        hostname = "localhost"
        try:
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30,
            )
            stdout = result.stdout
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1
    else:
        hostname = host
        rc, stdout = _ssh_run(host, script)
        if rc != 0 and not stdout.strip():
            print("Error: failed to connect to %s" % host, file=sys.stderr)
            return 1

    print(_parse_doctor(stdout, hostname))

    parts = stdout.split(_SENTINEL + "\n")
    docker = parts[1].strip() if len(parts) > 1 else "MISSING"
    compose = parts[2].strip() if len(parts) > 2 else "MISSING"
    daemon = parts[3].strip() if len(parts) > 3 else "NOT_RUNNING"
    if "Docker" not in docker or "Docker Compose" not in compose or daemon != "OK":
        print("\nMissing core dependencies. Install Docker and ensure the daemon is running.",
              file=sys.stderr)
        return 1

    return 0
