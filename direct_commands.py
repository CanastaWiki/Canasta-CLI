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


def _read_env_file(path, host):
    """Read and parse a .env file, returning a dict of key=value pairs."""
    env_path = os.path.join(path, ".env")
    try:
        if _is_localhost(host):
            with open(env_path) as f:
                content = f.read()
        else:
            rc, content = _ssh_run(host, "cat %s" % _shell_quote(env_path))
            if rc != 0:
                return {}
    except OSError:
        return {}

    result = {}
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("=", 1)
        if len(parts) == 2:
            key = parts[0].strip()
            value = parts[1].strip()
            if len(value) >= 2:
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
            result[key] = value
    return result


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
        return result.returncode, result.stdout
    except (subprocess.TimeoutExpired, OSError):
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
        return _check_running_k8s(instance_id)
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


def _check_running_k8s(instance_id):
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
    elif _check_running_k8s(inst_id):
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


@register("list")
def cmd_list(args):
    config_dir = _get_config_dir()
    conf_path = os.path.join(config_dir, "conf.json")

    if getattr(args, "cleanup", False):
        instances = _read_registry(conf_path)
        to_remove = [
            iid for iid, inst in instances.items()
            if not os.path.isdir(inst.get("path", ""))
        ]
        if to_remove:
            for iid in to_remove:
                del instances[iid]
            _write_registry(conf_path, instances)
            print("Removed stale entries: %s" % ", ".join(to_remove))

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

@register("version")
def cmd_version(args):
    script_dir = _get_script_dir()

    version_file = os.path.join(script_dir, "VERSION")
    try:
        with open(version_file) as f:
            version = f.read().strip()
    except OSError:
        version = "unknown"

    build_commit_file = os.path.join(script_dir, "BUILD_COMMIT")
    if os.path.isfile(build_commit_file):
        mode = "docker"
        try:
            with open(build_commit_file) as f:
                commit = f.read().strip()
            with open(os.path.join(script_dir, "BUILD_DATE")) as f:
                date = f.read().strip()
        except OSError:
            commit = "unknown"
            date = "unknown"
    else:
        mode = "native"
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

    key = getattr(args, "key", None)
    if key:
        if key in env_vars:
            print(env_vars[key])
        else:
            print("Key '%s' not found." % key)
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
    return _run_compose(inst_id, inst, ["up", "-d"])


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
    return _run_compose(inst_id, inst, ["up", "-d"])


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
