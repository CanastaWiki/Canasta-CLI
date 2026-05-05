"""Direct command implementations that bypass Ansible.

Commands registered here run as pure Python, avoiding the ~3-5s
overhead of ansible-playbook startup for simple operations.
"""

import json
import os
import re
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
    """Resolve instance from --id flag or cwd. Returns (id, inst_dict) or exits.

    Side effect: when the resolved instance has a `dockerHost` in the
    registry (set by `canasta create --docker-host=…` or by phase 2's
    auto-detect at create time, see #479), exports it as DOCKER_HOST
    in the process environment so any subsequent local `docker` /
    `docker compose` subprocess inherits it. Remote SSH-wrapped docker
    calls pick up the same value via _ssh_run's prefix logic.
    """
    from canasta import resolve_instance
    instance_id = getattr(args, "id", None)
    inst = resolve_instance(instance_id)
    docker_host = inst.get("dockerHost")
    if docker_host:
        os.environ["DOCKER_HOST"] = docker_host
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


def _k8s_namespace(instance_id):
    return "canasta-%s" % instance_id


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


def _stream_in_container(inst_id, inst, command, service="web"):
    """Run a command inside a container/pod, streaming combined
    stdout+stderr to the user's terminal as it arrives. Returns the
    process return code.

    Used by long-running maintenance commands (update.php, runJobs.php,
    rebuildData.php, ad-hoc maintenance scripts) so the operator can
    distinguish 'still working' from 'stuck' without having to drop
    down to `docker compose exec` directly. See issue #433.

    `stdbuf -oL` is prepended to force line-buffered stdout from any
    coreutils-style child the script spawns; PHP CLI is line-buffered
    by default but pipelines through grep/sed/awk are not, and a
    single un-flushed pipe ruins the stream.
    """
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    orchestrator = inst.get("orchestrator", "compose")
    wrapped = "stdbuf -oL %s" % command

    if orchestrator in ("kubernetes", "k8s"):
        ns = _k8s_namespace(inst_id)
        pod = _k8s_get_pod(ns, service)
        if not pod:
            print(
                "Error: no running pod found for service '%s'" % service,
                file=sys.stderr,
            )
            return 1
        argv = [
            "kubectl", "exec", pod, "-n", ns, "--",
            "/bin/bash", "-c", wrapped,
        ]
        cwd = None
    elif _is_localhost(host):
        argv = [
            "docker", "compose", "exec", "-T", service,
            "/bin/bash", "-c", wrapped,
        ]
        cwd = path or None
    else:
        target = _resolve_ssh_target(host)
        argv = ["ssh"] + _ssh_args() + [
            target,
            "cd %s && docker compose exec -T %s /bin/bash -c %s" % (
                _shell_quote(path), service, _shell_quote(wrapped),
            ),
        ]
        cwd = None

    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1

    try:
        for line in iter(proc.stdout.readline, ""):
            sys.stdout.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 130
    return proc.wait()


def _normalize_script_args(args):
    """REMAINDER positionals come back as a list; collapse to a single
    space-joined string. Empty list / None / blanks all map to ''."""
    val = getattr(args, "script_args", None)
    if val is None:
        return ""
    if isinstance(val, list):
        return " ".join(val).strip()
    return str(val).strip()


# Maintenance script paths must look like a php file path: alnum,
# slash, dot, underscore, hyphen, colon, space (for arguments after
# the script name). Same character class the playbook used.
_MAINT_PATH_RE = re.compile(r"^[a-zA-Z0-9/_. :-]+$")


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
    # ForwardAgent in the fallback matches the default canasta.py
    # plants when running through Ansible — direct commands that SSH
    # to a remote should expose the same agent so any forge auth
    # (gitops one-shots, future scripts) works the same way.
    # ServerAliveInterval keeps long-running commands (e.g.
    # `helm upgrade --wait --timeout 10m` from `canasta scale`) from
    # tripping a NAT / firewall idle drop and reporting a bogus
    # "Broken pipe" while the remote is still working.
    extra = os.environ.get(
        "ANSIBLE_SSH_ARGS",
        "-o StrictHostKeyChecking=accept-new -o ForwardAgent=yes "
        "-o ServerAliveInterval=30 -o ServerAliveCountMax=20",
    )
    return extra.split() if extra else []


def _ssh_run(host, cmd):
    # `host` may be a canasta short name registered via `canasta host
    # add` rather than something ~/.ssh/config or DNS knows about.
    # _resolve_ssh_target maps short names to their actual SSH target
    # (user@hostname) via hosts.yml; if there's no match it returns
    # `host` unchanged, so real hostnames/IPs/user@host strings keep
    # working. Without this, `canasta argocd password --host node1`
    # ends up running `ssh node1 …` which fails with "Could not
    # resolve hostname node1" even though canasta knows the mapping.
    target = _resolve_ssh_target(host)
    # Propagate DOCKER_HOST (set by _resolve_instance from the
    # registry's dockerHost field) to the remote so docker / docker
    # compose calls there honor the rootless socket. SSH does NOT
    # pass env vars by default; prepending the assignment is portable
    # and works for any cmd that ever shells out to docker.
    docker_host = os.environ.get("DOCKER_HOST")
    if docker_host:
        cmd = "DOCKER_HOST=%s %s" % (_shell_quote(docker_host), cmd)
    full_cmd = ["ssh"] + _ssh_args() + [target, cmd]
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


# --- hosts.yml registry (shared by host commands and SSH targeting) ---

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


# --- SSH target resolution + remote-file helpers (used widely) ---

def _resolve_ssh_target(host):
    """Map a registered host short name to its SSH target.

    If `host` matches an entry in hosts.yml, return user@host (or
    bare host if no ansible_user). Otherwise return `host` unchanged
    — the caller can rely on ~/.ssh/config to resolve it.
    """
    if not host or _is_localhost(host):
        return host
    data = _read_hosts_yml()
    if data is None:
        return host
    entry = (data.get("all", {}) or {}).get("hosts", {}).get(host) or {}
    target = entry.get("ansible_host") or host
    user = entry.get("ansible_user")
    return ("%s@%s" % (user, target)) if user else target



def _read_remote_or_local_file(path, host):
    """Read a file from localhost or a remote host. Returns content or
    None on error."""
    if _is_localhost(host):
        try:
            with open(path) as f:
                return f.read()
        except OSError as e:
            print("Error reading %s: %s" % (path, e), file=sys.stderr)
            return None
    rc, content = _ssh_run(host, "cat %s 2>/dev/null" % _shell_quote(path))
    if rc != 0:
        print("Error reading remote %s" % path, file=sys.stderr)
        return None
    return content


def _write_remote_or_local_file(path, host, content):
    """Write content to path on localhost or a remote host. Returns True
    on success."""
    if _is_localhost(host):
        try:
            with open(path, "w") as f:
                f.write(content)
            return True
        except OSError as e:
            print("Error writing %s: %s" % (path, e), file=sys.stderr)
            return False
    target = _resolve_ssh_target(host)
    ssh_cmd = ["ssh"] + _ssh_args() + [target, "cat > %s" % _shell_quote(path)]
    try:
        result = subprocess.run(
            ssh_cmd, input=content, text=True,
            capture_output=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print("Error writing remote %s: %s" % (path, e), file=sys.stderr)
        return False
    if result.returncode != 0:
        print(
            "Error writing remote %s: %s"
            % (path, (result.stderr or "").strip()),
            file=sys.stderr,
        )
        return False
    return True
