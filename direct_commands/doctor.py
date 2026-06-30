"""doctor command — dependency checks."""

import os
import subprocess
import sys


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
cat /proc/sys/net/ipv4/ip_unprivileged_port_start 2>/dev/null || echo unknown; echo "$D"
runtime="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"; _sock=""; for s in "$runtime/podman/podman.sock" "$runtime/docker.sock"; do if [ -S "$s" ]; then _sock="unix://$s"; break; fi; done; echo "$_sock"; echo "$D"
command -v canasta >/dev/null 2>&1 && { canasta version >/dev/null 2>&1 && echo OK || echo BROKEN; } || echo MISSING; echo "$D"
cdir="$(dirname "$(readlink -f "$(command -v canasta 2>/dev/null)" 2>/dev/null)" 2>/dev/null)"; if [ -n "$cdir" ] && [ -d "$cdir/.git" ]; then { [ -w "$cdir/.git" ] && echo WRITABLE || echo NOT_WRITABLE; } else echo NA; fi
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
    unpriv_port_start = p(16) if len(parts) > 16 else "unknown"
    rootless_sock = p(17) if len(parts) > 17 else ""
    # canasta probe is appended after rootless (index 18) so adding it
    # didn't shift the existing positional indices above.
    host_canasta = p(18) if len(parts) > 18 else "MISSING"
    # Writability of the native install dir's .git — the precondition for
    # `canasta upgrade`'s self-update (git fetch). NA on docker installs.
    selfupdate = p(19) if len(parts) > 19 else "NA"

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
        try:
            port_floor = int(unpriv_port_start)
        except (TypeError, ValueError):
            port_floor = None
        if port_floor is not None and port_floor > 80:
            lines.append(
                "  Privileged ports: BLOCKED — "
                "net.ipv4.ip_unprivileged_port_start=%d. Rootless Docker "
                "cannot bind Canasta's port 80/443. Fix with:\n"
                "                     sudo sysctl "
                "net.ipv4.ip_unprivileged_port_start=80\n"
                "                     echo "
                "net.ipv4.ip_unprivileged_port_start=80 | sudo tee "
                "/etc/sysctl.d/canasta-privport.conf"
                % port_floor
            )
        elif port_floor is not None:
            lines.append(
                "  Privileged ports: OK (ip_unprivileged_port_start=%d)"
                % port_floor
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
    # Backup scheduling writes a host crontab entry on the host where the
    # instance runs — the local/remote host for Compose, the control-plane
    # node for Kubernetes — so crontab is needed there for both orchestrators.
    lines.append("Scheduled backups (optional):")
    lines.append("  crontab:         %s" % (
        "OK" if crontab == "OK"
        else "not installed (install cron to use 'canasta backup schedule'; "
             "the schedule is a host crontab entry on the instance's host, "
             "including the control-plane node for Kubernetes)"))
    # Scheduled backups run `canasta backup create` on the host via the
    # crontab, so a runnable canasta must exist there (any flavor —
    # native or docker). BROKEN = the command exists but doesn't run.
    lines.append("  canasta on host: %s" % (
        "OK" if host_canasta == "OK"
        else "installed but not runnable" if host_canasta == "BROKEN"
        else "not installed (scheduled backups run 'canasta backup create' "
             "on the host; install with 'canasta install canasta -H <host>' "
             "or canasta-docker)"))

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
        canasta_member = "canasta" in groups.split()
        if canasta_member:
            lines.append("  canasta group:   OK (member)")
        else:
            lines.append(
                "  canasta group:   NOT A MEMBER — 'canasta upgrade' "
                "self-updates by writing the install dir; without membership "
                "the git fetch is skipped and the CLI silently stays on the "
                "old version. Add with: sudo usermod -aG canasta $USER "
                "(then log out and back in)"
            )
    else:
        os_label = host_os if host_os and host_os != "unknown" else "this OS"
        lines.append(
            "  www-data group:  N/A (Docker Desktop handles UID mapping on %s)"
            % os_label
        )

    # Direct self-update precondition: can the operator write the native
    # install dir's .git? NA on docker installs / no native canasta.
    if selfupdate == "NOT_WRITABLE":
        lines.append(
            "  Self-update:     BLOCKED — the canasta install dir's .git is "
            "not writable by you, so 'canasta upgrade' silently skips the "
            "self-update and the CLI stays on the old version (see "
            "'canasta group' above)"
        )
    elif selfupdate == "WRITABLE":
        lines.append("  Self-update:     OK (install dir writable)")

    return "\n".join(lines)


# service name -> the COMPOSE_PROFILE that must be active for it to be managed.
# Inverted from _MANAGED_PROFILE_SERVICES, plus db -> internal-db. Services not
# listed (web, caddy) are profile-less / always-on. Used to flag containers
# that are running but whose profile isn't in COMPOSE_PROFILES.
_SERVICE_PROFILE = {
    svc: prof
    for prof, svcs in _helpers._MANAGED_PROFILE_SERVICES.items()
    for svc in svcs
}
_SERVICE_PROFILE["db"] = "internal-db"


def _consistency_warnings(env, current_profiles, running_services, uses_cirrus):
    """Pure: warnings about config/runtime drift for a Compose instance.

    - COMPOSE_PROFILES that disagrees with what sync would derive from the
      flags + DB mode.
    - A container running under a profile that isn't active (unmanaged — a
      stop/start won't restore it).
    - CirrusSearch configured in settings while Elasticsearch is disabled.
    """
    warns = []
    active = set(current_profiles)

    desired = _helpers._reconcile_compose_profiles(env, list(current_profiles))
    if sorted(desired) != sorted(current_profiles):
        missing = sorted(p for p in desired if p not in active)
        stale = sorted(p for p in current_profiles if p not in desired)
        detail = []
        if missing:
            detail.append("should add %s" % ", ".join(missing))
        if stale:
            detail.append("should drop %s" % ", ".join(stale))
        warns.append(
            "COMPOSE_PROFILES out of sync with the feature flags (%s)"
            % "; ".join(detail))

    for svc in running_services:
        prof = _SERVICE_PROFILE.get(svc)
        if prof and prof not in active:
            warns.append(
                "'%s' is running but its profile '%s' is not in "
                "COMPOSE_PROFILES — it is unmanaged, and a stop/start cycle "
                "won't bring it back" % (svc, prof))

    if uses_cirrus and "elasticsearch" not in active:
        warns.append(
            "CirrusSearch is configured (config/settings) but the "
            "'elasticsearch' profile is inactive — search depends on an "
            "unmanaged Elasticsearch; set CANASTA_ENABLE_ELASTICSEARCH=true")

    return warns


def _gather_runtime(path, host):
    """(running_services, uses_cirrus) for an instance, localhost or remote."""
    d = _helpers._SENTINEL
    qpath = _helpers._shell_quote(path)
    script = (
        "cd %(p)s 2>/dev/null && "
        "docker compose ps --services --status running 2>/dev/null; "
        "echo '%(d)s'; "
        "grep -rqi cirrussearch %(p)s/config/settings 2>/dev/null "
        "&& echo USES_CIRRUS || echo NO_CIRRUS"
    ) % {"p": qpath, "d": d}
    if _helpers._is_localhost(host):
        try:
            out = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except (subprocess.TimeoutExpired, OSError):
            return [], False
    else:
        rc, out = _helpers._ssh_run(host, script)
        if rc != 0 and not out.strip():
            return [], False
    parts = out.split(d + "\n")
    head = parts[0].strip() if parts else ""
    running = [s for s in head.split("\n") if s.strip()]
    uses_cirrus = len(parts) > 1 and "USES_CIRRUS" in parts[1]
    return running, uses_cirrus


def _instance_consistency_lines(inst):
    """doctor lines for an instance's config<->runtime consistency, or [] when
    not applicable (no instance / not Compose / unreadable .env)."""
    if not inst or inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        return []
    path = inst.get("path", "")
    host = inst.get("host") or "localhost"
    if not path:
        return []
    content = _helpers._read_env_content(path, host)
    if not content:
        return []
    env = {
        k: v for k, v, c in _helpers._parse_env_entries(content) if not c and k
    }
    current = [
        p.strip() for p in env.get("COMPOSE_PROFILES", "").split(",")
        if p.strip()
    ]
    running, uses_cirrus = _gather_runtime(path, host)
    warns = _consistency_warnings(env, current, running, uses_cirrus)
    lines = ["", "Instance consistency (%s):" % inst.get("id", "?")]
    if warns:
        lines += ["  WARN: %s" % w for w in warns]
        lines.append("  Run 'canasta reconcile' to fix.")
    else:
        lines.append(
            "  OK (profiles, running services, and search backend agree)")
    return lines


@register("doctor")
def cmd_doctor(args):
    host = getattr(args, "host", None)
    inst_id = getattr(args, "id", None)
    # Resolve the instance (--id wins, then cwd-match) so we can both derive
    # the host to check — matching the "instance-aware default" of `canasta
    # status` / `version` — and run the per-instance consistency checks below.
    conf_path = os.path.join(_helpers._get_config_dir(), "conf.json")
    instances = _helpers._read_registry(conf_path)
    inst = None
    if inst_id:
        if inst_id not in instances:
            print(
                "Error: Instance '%s' not found in registry" % inst_id,
                file=sys.stderr,
            )
            return 1
        inst = instances[inst_id]
    else:
        _, inst = _helpers._resolve_instance_by_cwd(args)
    if not host and inst:
        host = inst.get("host") or "localhost"
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

    for line in _instance_consistency_lines(inst):
        print(line)

    parts = stdout.split(_helpers._SENTINEL + "\n")
    docker = parts[1].strip() if len(parts) > 1 else "MISSING"
    compose = parts[2].strip() if len(parts) > 2 else "MISSING"
    daemon = parts[3].strip() if len(parts) > 3 else "NOT_RUNNING"
    if "Docker" not in docker or "Docker Compose" not in compose or daemon != "OK":
        print("\nMissing core dependencies. Install Docker and ensure the daemon is running.",
              file=sys.stderr)
        return 1

    return 0
