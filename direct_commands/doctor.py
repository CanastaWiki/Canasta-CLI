"""doctor command — dependency checks."""

import os
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


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
command -v crontab >/dev/null 2>&1 && echo OK || echo MISSING; echo "$D"
uname -s 2>/dev/null || echo unknown; echo "$D"
python3 -c "import os; mem=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')//(1024**3); print(str(mem)+' GB')" 2>/dev/null || echo unknown; echo "$D"
df -h / | awk 'NR==2{print $4}' 2>/dev/null || echo unknown; echo "$D"
runtime="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"; for sock in "$runtime/podman/podman.sock" "$runtime/docker.sock"; do if [ -S "$sock" ]; then echo "unix://$sock"; exit 0; fi; done; echo ""
"""


def _parse_doctor(stdout, hostname):
    d = _helpers._SENTINEL
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
    crontab = p(12)
    host_os = p(13)
    memory = p(14)
    disk = p(15) if len(parts) > 15 else "unknown"
    rootless_sock = p(16) if len(parts) > 16 else ""

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
    if rootless_sock:
        lines.append(
            "  Rootless socket: %s "
            "(canasta create will auto-set --docker-host to this)"
            % rootless_sock
        )
    else:
        lines.append(
            "  Rootless socket: none detected "
            "(default /var/run/docker.sock)"
        )

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
    # Host crontab is only relevant for Compose `canasta backup schedule`.
    # On Kubernetes, scheduled backups run as a CronJob in-cluster and
    # don't depend on host crontab.
    lines.append("Scheduled backups, Compose only (optional):")
    lines.append("  crontab:         %s" % (
        "OK" if crontab == "OK"
        else "not installed (install cron to use canasta backup schedule "
             "on Compose; K8s uses an in-cluster CronJob instead)"))

    lines.append("")
    lines.append("System:")
    lines.append("  Memory:          %s" % memory)
    lines.append("  Disk (/ avail):  %s" % disk)
    # On macOS, Docker Desktop handles UID remapping transparently — there
    # is no www-data user/group on the host and membership is irrelevant.
    # Only report membership on Linux, where host-side file permissions
    # matter for volume mounts into the Canasta containers.
    if host_os == "Linux":
        www_member = "www-data" in groups.split()
        if www_member:
            lines.append("  www-data group:  OK (member)")
        else:
            lines.append(
                "  www-data group:  NOT A MEMBER — Canasta containers write "
                "files as www-data; add yourself with: "
                "sudo usermod -aG www-data $USER (then log out and back in, "
                "or start a new shell, for the change to take effect)"
            )
    else:
        os_label = host_os if host_os and host_os != "unknown" else "this OS"
        lines.append(
            "  www-data group:  N/A (Docker Desktop handles UID mapping on %s)"
            % os_label
        )

    return "\n".join(lines)


@register("doctor")
def cmd_doctor(args):
    host = getattr(args, "host", None)
    inst_id = getattr(args, "id", None)
    # When the caller doesn't pin a host explicitly, derive it from the
    # registry: --id wins, then cwd-match, then fall back to localhost.
    # Matches the "instance-aware default" behavior of `canasta status`,
    # `version`, and other direct commands so users in an instance
    # directory get its host checked instead of theirs.
    if not host:
        conf_path = os.path.join(_helpers._get_config_dir(), "conf.json")
        instances = _helpers._read_registry(conf_path)
        if inst_id:
            if inst_id not in instances:
                print(
                    "Error: Instance '%s' not found in registry" % inst_id,
                    file=sys.stderr,
                )
                return 1
            host = instances[inst_id].get("host") or "localhost"
        else:
            cwd = os.path.abspath(
                os.environ.get("CANASTA_HOST_PWD") or os.getcwd()
            )
            for inst in instances.values():
                p = inst.get("path", "")
                if p and os.path.abspath(p) == cwd:
                    host = inst.get("host") or "localhost"
                    break
    script = _DOCTOR_SCRIPT % {"delim": _helpers._SENTINEL}

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
        rc, stdout = _helpers._ssh_run(host, script)
        if rc != 0 and not stdout.strip():
            print("Error: failed to connect to %s" % host, file=sys.stderr)
            return 1

    print(_parse_doctor(stdout, hostname))

    parts = stdout.split(_helpers._SENTINEL + "\n")
    docker = parts[1].strip() if len(parts) > 1 else "MISSING"
    compose = parts[2].strip() if len(parts) > 2 else "MISSING"
    daemon = parts[3].strip() if len(parts) > 3 else "NOT_RUNNING"
    if "Docker" not in docker or "Docker Compose" not in compose or daemon != "OK":
        print("\nMissing core dependencies. Install Docker and ensure the daemon is running.",
              file=sys.stderr)
        return 1

    return 0
