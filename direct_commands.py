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
