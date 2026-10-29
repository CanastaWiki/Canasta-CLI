"""doctor command — dependency checks."""

import datetime
import os
import re
import shlex
import subprocess
import sys

import canasta_caddyfile
import canasta_edge_render
import canasta_web_cause
import yaml


from . import _helpers
from ._helpers import register


# Seconds any one slow probe command may take, where the host has
# timeout(1); a busy Docker daemon can stall `docker info` and
# `docker system df`. The whole probe gets PROBE_TIMEOUT.
PROBE_COMMAND_TIMEOUT = 10
PROBE_TIMEOUT = 120

# Run against the native install dir: its group, the directories the
# operator cannot write (a checkout leaves files in those unwritten; the
# virtualenv is pip's, as in the self-update's check), and,
# for a self-update-managed install (BUILD_COMMIT present), how many
# tracked files differ from HEAD.
_INSTALL_PROBE = """
import grp, os, subprocess, sys
d = sys.argv[1]
print("group=" + grp.getgrgid(os.stat(d).st_gid).gr_name)
bad = []
for r, dirs, _ in os.walk(d):
    if r == d and ".venv" in dirs:
        dirs.remove(".venv")
    if not os.access(r, os.W_OK):
        bad.append(r)
print("unwritable=%d %s" % (len(bad), bad[0] if bad else ""))
if os.path.isfile(os.path.join(d, "BUILD_COMMIT")):
    st = subprocess.run(
        ["git", "-C", d, "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, timeout=30)
    if st.returncode == 0:
        print("changed=%d" % len([x for x in st.stdout.splitlines() if x.strip()]))
"""

_DOCTOR_SCRIPT = r"""
D='%(delim)s'
_t() { if command -v timeout >/dev/null 2>&1; then timeout %(cmd_timeout)d "$@"; else "$@"; fi; }
_timedout() { [ "$1" -eq 124 ] || [ "$1" -eq 143 ]; }
_check() { ok=$1; fail=$2; shift 2; _t "$@" >/dev/null 2>&1; r=$?; if [ $r -eq 0 ]; then echo "$ok"; elif _timedout $r; then echo TIMEOUT; else echo "$fail"; fi; }
python3 --version 2>&1 || echo MISSING; echo "$D"
docker --version 2>&1 || echo MISSING; echo "$D"
docker compose version 2>&1 || echo MISSING; echo "$D"
_check OK NOT_RUNNING docker info; echo "$D"
id -nG 2>/dev/null || echo ""; echo "$D"
kubectl version --client --output=yaml >/dev/null 2>&1 && echo OK || echo MISSING; echo "$D"
helm version --short 2>/dev/null || echo MISSING; echo "$D"
k3s --version 2>/dev/null || echo MISSING; echo "$D"
_check REACHABLE UNREACHABLE kubectl cluster-info; echo "$D"
_check INSTALLED MISSING kubectl get deployment argocd-server -n argocd; echo "$D"
git --version 2>/dev/null || echo MISSING; echo "$D"
git-crypt --version 2>/dev/null && echo OK || echo MISSING; echo "$D"
command -v crontab >/dev/null 2>&1 && echo OK || echo MISSING; echo "$D"
uname -s 2>/dev/null || echo unknown; echo "$D"
python3 -c "import os; mem=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')//(1024**3); print(str(mem)+' GB')" 2>/dev/null || echo unknown; echo "$D"
_store=$(_t docker info --format '{{.DockerRootDir}}' 2>/dev/null); _timedout $? || [ -n "$_store" ] || _store=$(_t podman info --format '{{.Store.GraphRoot}}' 2>/dev/null); [ -n "$_store" ] && [ -d "$_store" ] || _store=/; printf '%%s|%%s\n' "$(df -h "$_store" 2>/dev/null | awk 'NR==2{print $4}')" "$_store"; echo "$D"
cat /proc/sys/net/ipv4/ip_unprivileged_port_start 2>/dev/null || echo unknown; echo "$D"
runtime="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"; _sock=""; for s in "$runtime/docker.sock" "$runtime/podman/podman.sock"; do if [ -S "$s" ]; then _sock="unix://$s"; break; fi; done; echo "$_sock"; echo "$D"
command -v canasta >/dev/null 2>&1 && _check OK BROKEN canasta version || echo MISSING; echo "$D"
cdir="$(dirname "$(readlink -f "$(command -v canasta 2>/dev/null)" 2>/dev/null)" 2>/dev/null)"; if [ -n "$cdir" ] && [ -d "$cdir/.git" ]; then { [ -w "$cdir/.git" ] && echo WRITABLE || echo NOT_WRITABLE; echo "dir=$cdir"; python3 -c @INSTALL_PROBE@ "$cdir" 2>/dev/null; } else echo NA; fi; echo "$D"
command -v sops >/dev/null 2>&1 && echo OK || echo MISSING; echo "$D"
command -v age-keygen >/dev/null 2>&1 && echo OK || echo MISSING; echo "$D"
command -v podman >/dev/null 2>&1 && podman --version 2>/dev/null || echo MISSING; echo "$D"
command -v sudo >/dev/null 2>&1 && sudo --version 2>/dev/null | head -1 || echo MISSING; echo "$D"
command -v sudo >/dev/null 2>&1 && { sudo -n true >/dev/null 2>&1 && echo OK || echo PASSWORD_REQUIRED; } || echo MISSING; echo "$D"
command -v podman-compose >/dev/null 2>&1 && podman-compose version 2>/dev/null || echo MISSING; echo "$D"
_df=$(_t docker system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null); r=$?; if [ $r -eq 0 ]; then echo "$_df"; elif _timedout $r; then echo unknown; else _t podman system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null || echo unknown; fi; echo "$D"
_n=$(getent group 33 2>/dev/null | cut -d: -f1); case " $(id -G 2>/dev/null) " in *" 33 "*) echo "MEMBER|$_n" ;; *) echo "NOT_MEMBER|$_n" ;; esac
""".replace(
    "@INSTALL_PROBE@", shlex.quote(_INSTALL_PROBE).replace("%", "%%"))


# ansible-lint materializes a stub for every module named in a project's
# `mock_modules`, so linting can resolve the name without the collection
# installed. Written into the operator's own collection tree — which older
# versions did, rather than their own cache — the stub replaces the real
# module inside a collection that still reports its pinned version. So
# `ansible-galaxy collection list` shows it healthy, and neither `install`
# nor `install --upgrade` repairs it: both see the version already present
# and skip. Only `--force` rewrites the files.
#
# The stub's argspec is empty, so the module rejects every option it is
# given: "Unsupported parameters for (kubernetes.core.k8s) module:
# definition, state. Supported parameters include: ." It surfaces only
# against a remote host — a controller-side call is handled by the
# collection's action plugin, which is not mocked, and never runs the stub.
_MOCK_MODULE_MARKER = "ansible-lint (@nobody)"


def _pinned_collections():
    """(namespace, name) for each collection requirements.yml pins."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "requirements.yml")
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return []
    out = []
    for entry in data.get("collections", []) or []:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.count(".") == 1:
            out.append(tuple(name.split(".")))
    return out


def _collection_search_paths():
    """Where ansible looks for installed collections, in its own order."""
    env = (os.environ.get("ANSIBLE_COLLECTIONS_PATH")
           or os.environ.get("ANSIBLE_COLLECTIONS_PATHS") or "")
    paths = [p for p in env.split(os.pathsep) if p]
    if not paths:
        home = os.environ.get("ANSIBLE_HOME") or os.path.expanduser("~/.ansible")
        paths = [os.path.join(home, "collections"),
                 "/usr/share/ansible/collections"]
    return paths


def _mocked_collection_modules():
    """['ns.name: module', ...] for lint stubs found in installed collections.

    Only the collections requirements.yml pins are inspected: those are the
    ones the CLI executes, and a stub anywhere else is not its problem.
    """
    found = []
    for namespace, name in _pinned_collections():
        for base in _collection_search_paths():
            mod_dir = os.path.join(
                base, "ansible_collections", namespace, name,
                "plugins", "modules")
            if not os.path.isdir(mod_dir):
                continue
            try:
                entries = sorted(os.listdir(mod_dir))
            except OSError:
                continue
            for entry in entries:
                if not entry.endswith(".py"):
                    continue
                try:
                    with open(os.path.join(mod_dir, entry), errors="ignore") as f:
                        head = f.read(2048)
                except OSError:
                    continue
                if _MOCK_MODULE_MARKER in head:
                    found.append("%s.%s: %s" % (namespace, name, entry[:-3]))
            # First match on the search path is the one ansible would use.
            break
    return found


def _collection_integrity_lines():
    """Report collections whose modules have been replaced by lint stubs."""
    mocked = _mocked_collection_modules()
    if not mocked:
        return []
    return [
        "",
        "Controller (this machine):",
        "  Ansible collections: %d module(s) replaced by ansible-lint stubs "
        "— %s. These reject every option they are given when they run on a "
        "remote host, so Kubernetes commands against --host fail with "
        "\"Supported parameters include: .\". The collection still reports "
        "its pinned version, and 'ansible-galaxy collection install "
        "--upgrade' will not repair it. Fix with: ansible-galaxy collection "
        "install --force -r requirements.yml"
        % (len(mocked), ", ".join(mocked)),
    ]


def _install_escalation_blocked(sudo_version, sudo_nopasswd):
    """True when `canasta install` cannot escalate on this host.

    Ansible's become drives GNU sudo: it passes its own prompt via -p and
    waits for that exact string before sending the password. sudo-rs, the
    Rust reimplementation shipped as the default sudo on recent Ubuntu,
    does not present that prompt, so become times out whether or not a
    password is supplied. With passwordless sudo there is no prompt to
    match and escalation succeeds regardless of the implementation.
    """
    if sudo_nopasswd == "OK":
        return False
    return "sudo-rs" in (sudo_version or "").lower()


def _has_container_runtime(docker, compose, daemon, podman_version):
    """True when the host can actually run containers.

    podman_version is matched on the expected output rather than on the
    absence of the MISSING sentinel — a failed probe still prints text
    containing the word "podman".
    """
    if "Docker" in docker and "Docker Compose" in compose and daemon in ("OK", "TIMEOUT"):
        return True
    return "podman version" in podman_version.lower()


def _parse_system_df(df_out):
    """Reclaimable image and build-cache sizes from `docker system df`.

    Returns Docker's own size strings as (images, build_cache), or None when
    the probe produced nothing usable.

    The Local Volumes row is deliberately dropped rather than summed in:
    `canasta stop` runs `docker compose down`, which removes the containers,
    so a stopped instance's database volume has nothing referencing it and
    Docker reports it as reclaimable. Presenting that as recoverable space
    invites a prune that destroys the wiki.
    """
    if not df_out or df_out.strip() == "unknown":
        return None
    found = {"images": None, "build cache": None}
    for line in df_out.splitlines():
        kind, sep, value = line.partition("|")
        if not sep:
            continue
        key = kind.strip().lower()
        if key in found:
            # Reclaimable reads like "5.1GB (60%)"; keep the size.
            found[key] = value.strip().split(" ")[0]
    if found["images"] is None and found["build cache"] is None:
        return None
    return (found["images"] or "0B", found["build cache"] or "0B")


def _is_zero_size(size):
    """True when a Docker size string is zero. Unparseable reads as non-zero."""
    m = re.match(r"\s*([0-9.]+)", size or "")
    return m is not None and float(m.group(1)) == 0


def _reclaimable_line(df_out, k3s):
    """The System-section line for reclaimable image storage, or None.

    `docker system df` describes neither containerd's image store nor the
    in-cluster registry PVC, so on a cluster host the figure is labelled as
    the partial measurement it is instead of standing in for the total.

    k3s presence, not cluster reachability, is what decides that: kubectl
    follows the current kubeconfig context, which routinely names a cluster
    running somewhere else entirely.
    """
    k8s = k3s != "MISSING"
    parsed = _parse_system_df(df_out)
    if parsed is None:
        if not k8s:
            return None
        return ("  Reclaimable:     not measured (no Docker daemon); "
                "'canasta image prune' reclaims containerd images and the "
                "in-cluster registry")
    images, build_cache = parsed
    if _is_zero_size(images) and _is_zero_size(build_cache):
        amount = "none"
    else:
        amount = "%s images, %s build cache" % (images, build_cache)
    if k8s:
        amount += "; containerd and the in-cluster registry not measured"
    # `canasta image prune` leaves the build cache alone, and on a cluster
    # host it also keeps the Docker copies of images built for the registry.
    advice = []
    if k8s or not _is_zero_size(images):
        advice.append("images with 'canasta image prune'")
        if k8s and not _is_zero_size(images):
            advice[-1] += (" (it lists, but keeps, the Docker copies of "
                           "images built for Kubernetes)")
    if not _is_zero_size(build_cache):
        advice.append("build cache with 'docker builder prune'")
    if advice:
        amount += " — reclaim " + ", ".join(advice)
    return "  Reclaimable:     %s" % amount


def _timed_out(command):
    return "unknown ('%s' took over %d seconds)" % (command, PROBE_COMMAND_TIMEOUT)


def _doctor_probes(stdout):
    """Split the doctor probe output into its named fields."""
    d = _helpers._SENTINEL
    parts = stdout.split(d + "\n")

    def p(i):
        return parts[i].strip() if i < len(parts) else "unknown"

    return {
        "python": p(0),
        "docker": p(1),
        "compose": p(2),
        "daemon": p(3),
        "groups": p(4),
        "kubectl": p(5),
        "helm": p(6),
        "k3s": p(7),
        "cluster": p(8),
        "argocd": p(9),
        "git": p(10),
        "gitcrypt": p(11),
        "crontab": p(12),
        "host_os": p(13),
        "memory": p(14),
        "disk": p(15) if len(parts) > 15 else "unknown",
        "unpriv_port_start": p(16) if len(parts) > 16 else "unknown",
        "rootless_sock": p(17) if len(parts) > 17 else "",
        # canasta probe is appended after rootless (index 18) so adding it
        # didn't shift the existing positional indices above.
        "host_canasta": p(18) if len(parts) > 18 else "MISSING",
        # Writability of the native install dir's .git — the precondition for
        # `canasta upgrade`'s self-update (git fetch). NA on docker installs.
        "selfupdate": p(19) if len(parts) > 19 else "NA",
        # SOPS toolchain for encrypted K8s gitops secrets (appended, so it didn't
        # shift the positional indices above): sops on the gitops host, age on the
        # controller.
        "sops": p(20) if len(parts) > 20 else "MISSING",
        "age": p(21) if len(parts) > 21 else "MISSING",
        "podman_version": p(22) if len(parts) > 22 else "MISSING",
        # Privilege escalation, appended so the indices above are unchanged.
        "sudo_version": p(23) if len(parts) > 23 else "MISSING",
        "sudo_nopasswd": p(24) if len(parts) > 24 else "MISSING",
        "podman_compose_version": p(25) if len(parts) > 25 else "MISSING",
        # Reclaimable image storage, appended so the indices above are unchanged.
        "system_df": p(26) if len(parts) > 26 else "",
        # Membership of gid 33 and the host's name for it, as "MEMBER|tape".
        "web_gid": p(27) if len(parts) > 27 else "",
    }


def _doctor_core_lines(probes):
    """Core (required) section: container runtime and daemon."""
    lines = []
    python = probes["python"]
    docker = probes["docker"]
    compose = probes["compose"]
    daemon = probes["daemon"]
    unpriv_port_start = probes["unpriv_port_start"]
    rootless_sock = probes["rootless_sock"]
    lines.append("  Python 3:        %s" % (
        "OK (%s)" % python if "Python" in python else "MISSING"))
    lines.append("  Docker:          %s" % (
        "OK (%s)" % docker if "Docker" in docker else "MISSING"))
    lines.append("  Docker Compose:  %s" % (
        "OK (%s)" % compose if "Docker Compose" in compose else "MISSING"))
    lines.append("  Docker daemon:   %s" % (
        "OK (running)" if daemon == "OK"
        else _timed_out("docker info") if daemon == "TIMEOUT" else "NOT RUNNING"))
    if rootless_sock:
        if daemon != "OK":
            create_use = "canasta create will auto-set --docker-host to this"
        elif "podman" in rootless_sock:
            create_use = (
                "canasta create uses the Docker daemon instead; set "
                "CANASTA_CONTAINER_RUNTIME=podman to use Podman"
            )
        else:
            create_use = (
                "canasta create uses the Docker daemon unless --docker-host "
                "names this socket"
            )
        lines.append(
            "  Rootless socket: %s (%s)" % (rootless_sock, create_use)
        )
        try:
            port_floor = int(unpriv_port_start)
        except (TypeError, ValueError):
            port_floor = None
        if port_floor is not None and port_floor > 80 and "podman" in rootless_sock:
            lines.append(
                "  Privileged ports: BLOCKED — "
                "net.ipv4.ip_unprivileged_port_start=%d. The edge's systemd "
                "socket unit (canasta-edge.socket) cannot bind ports 80/443. "
                "Fix with:\n"
                "                     canasta install podman\n"
                "                     (or manually: sudo sysctl "
                "net.ipv4.ip_unprivileged_port_start=80\n"
                "                      && echo "
                "net.ipv4.ip_unprivileged_port_start=80 | sudo tee "
                "/etc/sysctl.d/canasta-privport.conf)"
                % port_floor
            )
        elif port_floor is not None and port_floor > 80:
            lines.append(
                "  Privileged ports: BLOCKED — "
                "net.ipv4.ip_unprivileged_port_start=%d. Rootless Docker's "
                "RootlessKit cannot bind ports 80/443 for the edge. Fix with:\n"
                "                     canasta install docker-rootless\n"
                "                     (or manually: sudo sysctl "
                "net.ipv4.ip_unprivileged_port_start=80\n"
                "                      && echo "
                "net.ipv4.ip_unprivileged_port_start=80 | sudo tee "
                "/etc/sysctl.d/canasta-privport.conf)"
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
    return lines


def _doctor_podman_lines(probes):
    """Podman version, with the known-bad versions flagged."""
    lines = []
    podman_version = probes["podman_version"]
    if "podman version" in podman_version.lower():
        ver = podman_version
        # Parse "podman version 4.3.1" → "4.3.1"
        m = re.search(r'(\d+\.\d+\.\d+)', ver)
        if m:
            ver_str = m.group(1)
            ver_parts = [int(x) for x in ver_str.split(".")]
            while len(ver_parts) < 3:
                ver_parts.append(0)
            if ver_parts < [4, 4, 0]:
                lines.append(
                    "  Podman:          %s — WARNING: podman < 4.4 has a "
                    "known Docker API bug that causes 'docker compose up' to "
                    "fail with EOF on container start. "
                    "Install podman >= 4.4, or pin the runtime for one "
                    "instance: compose_command=podman-compose in a local "
                    "instance's .env, or composeCommand in its registry "
                    "entry for a remote one."
                    % ver_str
                )
            else:
                lines.append(
                    "  Podman:          %s (OK)" % ver_str
                )
        else:
            lines.append(
                "  Podman:          %s (version unknown)" % ver
            )
    return lines


def _doctor_podman_compose_lines(probes):
    """podman-compose version, with the known-bad version flagged."""
    lines = []
    podman_compose_version = probes["podman_compose_version"]
    if podman_compose_version not in ("MISSING", ""):
        pc_ver = podman_compose_version
        # podman-compose version prints its own version first, then
        # shells out to podman:
        #   "podman-compose version 1.3.0"
        #   "podman version 5.4.2"
        # The regex is anchored on 'podman-compose version', so it
        # matches the first line either way.
        m = re.search(r'podman-compose version\s+(\d+\.\d+\.\d+)', pc_ver)
        if m:
            pc_ver_str = m.group(1)
            # 1.3.0.post1 matches the regex too and is flagged with it:
            # the substitution bug is in the 1.3.0 base, and warning on
            # the post release errs on the safe side.
            if pc_ver_str == "1.3.0":
                lines.append(
                    "  Podman Compose:  %s — WARNING: podman-compose 1.3.0 "
                    "has a known bug where ${VAR:-default} is passed "
                    "literally to containers when VAR is unset. This "
                    "breaks Canasta's docker-compose.yml defaults. "
                    "Upgrade to podman-compose >= 1.3.1 "
                    "(https://github.com/containers/podman-compose/"
                    "issues/1105)"
                    % pc_ver_str
                )
            else:
                lines.append(
                    "  Podman Compose:  %s" % pc_ver_str
                )
        else:
            # The version is unparsable and may span several lines;
            # keep the report aligned with just the first.
            lines.append(
                "  Podman Compose:  %s" % pc_ver.splitlines()[0]
            )
    return lines


def _doctor_kubernetes_lines(probes):
    """Kubernetes (optional) section."""
    lines = []
    kubectl = probes["kubectl"]
    helm = probes["helm"]
    k3s = probes["k3s"]
    cluster = probes["cluster"]
    argocd = probes["argocd"]
    lines.append("")
    lines.append("Kubernetes (optional):")
    lines.append("  kubectl:         %s" % ("OK" if kubectl.strip() == "OK" else "not installed"))
    lines.append("  Helm:            %s" % (
        "OK (%s)" % helm if helm != "MISSING" else "not installed"))
    lines.append("  k3s:             %s" % (
        "OK" if k3s != "MISSING" else "not installed"))
    lines.append("  Cluster:         %s" % (
        "reachable" if cluster.strip() == "REACHABLE"
        else _timed_out("kubectl cluster-info") if cluster.strip() == "TIMEOUT"
        else "not reachable"))
    lines.append("  Argo CD:         %s" % (
        "installed" if argocd.strip() == "INSTALLED"
        else _timed_out("kubectl") if argocd.strip() == "TIMEOUT" else "not found"))
    return lines


def _doctor_gitops_lines(probes):
    """GitOps (optional) section."""
    lines = []
    git = probes["git"]
    gitcrypt = probes["gitcrypt"]
    sops = probes["sops"]
    age = probes["age"]
    lines.append("")
    lines.append("GitOps (optional):")
    lines.append("  git:             %s" % (
        "OK (%s)" % git if "git version" in git else "not installed"))
    lines.append("  git-crypt:       %s" % (
        "OK" if gitcrypt == "OK" else "not installed"))
    # SOPS toolchain: sops encrypts Secret manifests on the gitops host; age
    # manages the operator key on the controller. Needed only for K8s gitops
    # with gitops_sops_secrets enabled. Install both with 'canasta install sops'.
    lines.append("  sops:            %s" % (
        "OK" if sops == "OK"
        else "not installed (needed on the gitops host for encrypted K8s "
             "secrets; 'canasta install sops')"))
    lines.append("  age:             %s" % (
        "OK" if age == "OK"
        else "not installed (operator key tool for encrypted K8s secrets; "
             "'canasta install sops')"))
    return lines


def _doctor_backup_lines(probes):
    """Scheduled backups (optional) section."""
    lines = []
    crontab = probes["crontab"]
    host_canasta = probes["host_canasta"]
    lines.append("")
    if probes.get("docker_mode"):
        # The crontab check is skipped with the other host checks (see
        # _doctor_system_lines). The wrapper running this command is the
        # host's canasta, and scheduled backups call it by this path.
        lines.append("Scheduled backups (optional):")
        cli_bin = os.environ.get("CANASTA_CLI_BIN")
        if cli_bin:
            lines.append("  canasta on host: OK (%s)" % cli_bin)
        return lines
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
        else _timed_out("canasta version") if host_canasta == "TIMEOUT"
        else "not installed (scheduled backups run 'canasta backup create' "
             "on the host; install with 'canasta install canasta -H <host>' "
             "or canasta-docker)"))
    return lines


def _disk_line(disk):
    """Free space on the filesystem holding container images.

    The probe reports `avail|path`, measuring the runtime's image store
    rather than `/`: rootless Docker and Podman keep images under the
    user's home, which is often a different filesystem.
    """
    avail, sep, path = disk.partition("|")
    if not sep or not avail.strip():
        return "unknown"
    path = path.strip() or "/"
    if path == "/":
        return "%s on /" % avail.strip()
    return "%s on %s (container image storage)" % (avail.strip(), path)


def _parse_install_probe(selfupdate):
    """Split the self-update probe into its .git writability status and
    the install-dir details reported after it."""
    status, *rest = selfupdate.splitlines() or [""]
    install = {"dir": "", "group": "", "unwritable": 0,
               "first_unwritable": "", "changed": 0}
    for line in rest:
        key, _, value = line.strip().partition("=")
        if key in ("dir", "group"):
            install[key] = value
        elif key == "unwritable":
            count, _, first = value.partition(" ")
            install["unwritable"] = int(count) if count.isdigit() else 0
            install["first_unwritable"] = first
        elif key == "changed" and value.isdigit():
            install["changed"] = int(value)
    return status.strip(), install


def _www_data_line(groups, web_gid):
    """The www-data line: membership of gid 33, whatever the host calls it.

    The containers write files as gid 33, which Debian names www-data and
    RHEL names tape.
    """
    status, sep, name = web_gid.partition("|")
    if not sep:
        status = "MEMBER" if "www-data" in groups.split() else "NOT_MEMBER"
        name = "www-data"
    if status == "MEMBER":
        if name and name != "www-data":
            return ("  www-data group:  OK (member of gid 33, '%s' on this host)"
                    % name)
        return "  www-data group:  OK (member)"
    if name == "www-data":
        host_name = ""
    elif name:
        host_name = " (gid 33, '%s' on this host)" % name
    else:
        host_name = " (gid 33, which no group on this host has)"
    if name:
        fix = "sudo usermod -aG %s $USER" % name
    else:
        fix = "sudo groupadd -g 33 www-data && sudo usermod -aG www-data $USER"
    return (
        "  www-data group:  NOT A MEMBER — Canasta containers write files as "
        "www-data%s; add yourself with: %s (then log out and back in, or "
        "start a new shell, for the change to take effect)" % (host_name, fix)
    )


def _doctor_system_lines(probes):
    """System section: resources, groups, self-update, sudo."""
    lines = []
    groups = probes["groups"]
    k3s = probes["k3s"]
    host_os = probes["host_os"]
    memory = probes["memory"]
    disk = probes["disk"]
    selfupdate = probes["selfupdate"]
    sudo_version = probes["sudo_version"]
    sudo_nopasswd = probes["sudo_nopasswd"]
    system_df = probes["system_df"]
    lines.append("")
    lines.append("System:")
    lines.append("  Memory:          %s" % memory)
    lines.append("  Disk free:       %s" % _disk_line(disk))
    reclaimable = _reclaimable_line(system_df, k3s)
    if reclaimable:
        lines.append(reclaimable)
    if probes.get("docker_mode"):
        lines.append(
            "  Host checks:     SKIPPED in docker mode — group membership, "
            "crontab and sudo are host settings the CLI container cannot "
            "see, so they are not checked"
        )
        return lines
    # On macOS, Docker Desktop handles UID remapping transparently — there
    # is no www-data user/group on the host and membership is irrelevant.
    # Only report membership on Linux, where host-side file permissions
    # matter for volume mounts into the Canasta containers.
    if host_os == "Linux":
        if probes.get("rootless_sock") and probes.get("daemon") != "OK":
            # Without a rootful daemon, create uses the rootless runtime,
            # which maps the containers' gid 33 to one of the user's own
            # subordinate gids, not to the host's gid 33.
            lines.append("  www-data group:  N/A (the rootless runtime maps "
                         "container groups to your own)")
        else:
            lines.append(_www_data_line(groups, probes.get("web_gid", "")))
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
    selfupdate, install = _parse_install_probe(selfupdate)
    repair = " && ".join(_helpers._install_repair_commands(
        install["dir"], install["group"] == "canasta"))
    if selfupdate == "NOT_WRITABLE":
        lines.append(
            "  Self-update:     BLOCKED — the canasta install dir's .git is "
            "not writable by you, so 'canasta upgrade' silently skips the "
            "self-update and the CLI stays on the old version (see "
            "'canasta group' above)"
        )
    elif install["unwritable"]:
        lines.append(
            "  Self-update:     BLOCKED — you cannot write %d of the "
            "directories in %s (first: %s), so 'canasta upgrade' cannot "
            "update every file. Repair with: %s"
            % (install["unwritable"], install["dir"],
               install["first_unwritable"], repair)
        )
    elif selfupdate == "WRITABLE":
        lines.append("  Self-update:     OK (install dir writable)")
    if install["changed"]:
        lines.append(
            "  CLI install:     INCOMPLETE — files in %s do not match the "
            "installed version (%d changed or missing), so commands can "
            "fail on them. Repair with: %s"
            % (install["dir"], install["changed"], repair)
        )

    # Every `canasta install` package needs root. Report when Ansible's
    # become cannot get it, rather than letting install time out with
    # "Host unreachable" and an Ansible-internal message.
    if _install_escalation_blocked(sudo_version, sudo_nopasswd):
        lines.append(
            "  Privilege esc.:  BLOCKED for 'canasta install' — this host "
            "uses sudo-rs (%s) and sudo asks for a password. Ansible's "
            "become drives GNU sudo's prompt, which sudo-rs does not "
            "present, so install times out however the password is "
            "supplied. Configure passwordless sudo, or install packages "
            "with the system package manager (the install roles are thin "
            "wrappers around apt/dnf)." % sudo_version
        )
    elif sudo_nopasswd == "OK":
        lines.append("  Privilege esc.:  OK (passwordless sudo)")
    return lines


def _parse_doctor(stdout, hostname, docker_mode=False):
    probes = _doctor_probes(stdout)
    probes["docker_mode"] = docker_mode
    lines = [
        "Canasta Dependency Check (%s)" % hostname,
        "=" * 52,
        "",
        "Core (required):",
    ]
    lines.extend(_doctor_core_lines(probes))
    lines.extend(_doctor_podman_lines(probes))
    lines.extend(_doctor_podman_compose_lines(probes))
    lines.extend(_doctor_kubernetes_lines(probes))
    lines.extend(_doctor_gitops_lines(probes))
    lines.extend(_doctor_backup_lines(probes))
    lines.extend(_doctor_system_lines(probes))
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


def _consistency_warnings(env, current_profiles, running_services, uses_cirrus,
                          template_literals=None):
    """Pure: warnings about config/runtime drift for a Compose instance.

    - COMPOSE_PROFILES that disagrees with what sync would derive from the
      flags + DB mode.
    - A container running under a profile that isn't active (unmanaged — a
      stop/start won't restore it).
    - CrowdSec enabled with no TRUSTED_PROXIES.
    - CirrusSearch configured in settings while Elasticsearch is disabled.
    - any env.template literal that differs from .env (the durable gitops
      source and the live config have diverged; a pull would reset .env to it).
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

    crowdsec_on = env.get("CANASTA_ENABLE_CROWDSEC", "").strip().lower() == "true"
    if crowdsec_on and not env.get("TRUSTED_PROXIES", "").strip():
        warns.append(
            "CrowdSec is enabled, but TRUSTED_PROXIES is not set. If "
            "this instance runs behind a proxy such as Cloudflare or Imperva, "
            "set it — otherwise CrowdSec sees only the proxy's addresses, so "
            "its scenarios cannot identify a real client and any ban it "
            "issues lands on a shared edge node and blocks every visitor "
            "behind it: 'canasta config set TRUSTED_PROXIES=cloudflare' "
            "(or imperva, or a comma-separated CIDR list for any other "
            "proxy). Leave it unset if clients connect to this host directly — trusting the "
            "header then would let any client forge its own source IP")

    drifted = sorted(
        k for k, tv in (template_literals or {}).items()
        if k in env and env[k] != tv)
    if drifted:
        detail = "; ".join(
            "%s (env.template=%s, .env=%s)" % (k, template_literals[k], env[k])
            for k in drifted)
        warns.append(
            "env.template disagrees with .env on: %s. A gitops pull "
            "re-renders .env from env.template, so these values would change. "
            "'canasta reconcile' keeps whichever of the two was edited since "
            "the last pull or push"
            % detail)

    return warns


def _gather_runtime(path, host, inst=None):
    """(running_services, uses_cirrus, env_template_literals) for an instance,
    localhost or remote. env_template_literals maps each KEY to the literal
    value pinned in env.template, excluding placeholder (KEY={{...}}) lines and
    comments; empty when not gitops / no env.template."""
    d = _helpers._SENTINEL
    qpath = _helpers._shell_quote(path)
    compose_cmd = _helpers._resolve_compose_cmd(inst or {"path": path})
    compose_str = " ".join(compose_cmd)
    script = (
        "cd %(p)s 2>/dev/null && "
        "%(c)s ps --services --status running 2>/dev/null; "
        "echo '%(d)s'; "
        "grep -rqi cirrussearch %(p)s/config/settings 2>/dev/null "
        "&& echo USES_CIRRUS || echo NO_CIRRUS; "
        "echo '%(d)s'; "
        "grep -E '^[A-Za-z_][A-Za-z0-9_]*=' %(p)s/env.template 2>/dev/null "
        "| grep -v '={{'"
    ) % {"p": qpath, "d": d, "c": compose_str}
    if _helpers._is_localhost(host):
        try:
            out = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except (subprocess.TimeoutExpired, OSError):
            return [], False, {}
    else:
        rc, out = _helpers._ssh_run(host, script)
        if rc != 0 and not out.strip():
            return [], False, {}
    parts = out.split(d + "\n")
    head = parts[0].strip() if parts else ""
    running = [s for s in head.split("\n") if s.strip()]
    uses_cirrus = len(parts) > 1 and "USES_CIRRUS" in parts[1]
    template_literals = {}
    if len(parts) > 2:
        for line in parts[2].strip().split("\n"):
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            key, val = line.split("=", 1)
            template_literals[key.strip()] = _helpers._unquote(val)
    return running, uses_cirrus, template_literals


def _instance_consistency_lines(inst):
    """doctor lines for an instance's config<->runtime consistency, or [] when
    not applicable (no instance / unreadable state)."""
    if not inst:
        return []
    if _helpers._is_k8s(inst):
        return _k8s_config_consistency_lines(inst)
    path = inst.get("path", "")
    host = inst.get("host") or "localhost"
    if not path:
        return []
    content = _helpers._read_env_content(path, host)
    if not content:
        return []
    env = _helpers.canasta_env_keys.modernize_values({
        k: v for k, v, c in _helpers._parse_env_entries(content) if not c and k
    })
    current = [
        p.strip() for p in env.get("COMPOSE_PROFILES", "").split(",")
        if p.strip()
    ]
    running, uses_cirrus, template_literals = _gather_runtime(path, host, inst)
    warns = _consistency_warnings(
        env, current, running, uses_cirrus, template_literals)
    lines = ["", "Instance consistency (%s):" % inst.get("id", "?")]
    if warns:
        lines += ["  WARN: %s" % w for w in warns]
        lines.append(
            "  Run 'canasta reconcile' to heal profile/runtime and image-tag "
            "drift; any other warning above names its own fix.")
    else:
        lines.append(
            "  OK (profiles, running services, and search backend agree)")
    return lines


# --- Origin TLS -------------------------------------------------------------
#
# A CDN in a non-strict origin mode serves visitors its own certificate
# whatever the origin presents, so an expired or internally-issued origin
# certificate shows no symptom until the CDN is switched to strict validation.

# Caddy renews well before this, so a certificate still this close to expiry
# means renewal is failing.
_TLS_RENEWAL_FLOOR_DAYS = 14

# Caddy's fallback when it cannot obtain a public certificate, and the CA
# the HAProxy edge signs its own certificates with.
_TLS_INTERNAL_ISSUER_MARKERS = ("caddy local authority", "canasta edge ca")

# Let's Encrypt's staging CA marks every issuer CN this way, e.g.
# "(STAGING) Artificial Amaranth YE1". Staging certificates are valid and
# unexpired but chain to an untrusted root, so browsers reject them —
# the same visible outcome as the internal-CA case above.
_TLS_STAGING_ISSUER_MARKERS = ("(staging)",)

# Traefik serves this self-signed certificate for any Ingress host that has
# no certificate of its own.
_TLS_TRAEFIK_DEFAULT_MARKERS = ("traefik default cert",)

# Names Caddy serves from its internal CA by design.
_TLS_LOCAL_NAMES = ("localhost",)
_TLS_LOCAL_SUFFIXES = (
    ".localhost", ".local", ".internal", ".test", ".example", ".invalid",
)

# Probed per SNI name: a host serving several domains can have one fall back
# while the others hold valid certificates.
_ORIGIN_TLS_SCRIPT = r"""
command -v openssl >/dev/null 2>&1 || { echo NO_OPENSSL; exit 0; }
printf '%%sTLSINTERNAL:%%s\n' '%(delim)s' "$(grep -lE '^[[:space:]]*tls[[:space:]]+internal([[:space:]]|$)' %(cfg)s 2>/dev/null | head -1)"
[ -f %(values)s ] && { printf '%%sVALUES:\n' '%(delim)s'; cat %(values)s; }
for n in %(names)s; do
  printf '%%sNAME:%%s\n' '%(delim)s' "$n"
  echo | openssl s_client -connect 127.0.0.1:%(port)s -servername "$n" \
      2>/dev/null | openssl x509 -noout -subject -issuer -enddate 2>/dev/null
done
"""


def _is_public_hostname(name):
    """True when `name` is a publicly resolvable domain, i.e. one that should
    hold a publicly trusted certificate."""
    n = (name or "").strip().lower().rstrip(".")
    if not n or n in _TLS_LOCAL_NAMES or "." not in n:
        return False
    if any(n.endswith(suffix) for suffix in _TLS_LOCAL_SUFFIXES):
        return False
    if ":" in n or re.match(r"^[0-9.]+$", n):  # IPv6 / IPv4 literal
        return False
    return True


def _parse_origin_tls(stdout):
    """[(name, issuer, not_after, subject)] from the probe output, or None
    when the host has no openssl. The fields are empty when nothing
    answered."""
    segments = stdout.split(_helpers._SENTINEL)
    if "NO_OPENSSL" in segments[0]:
        return None
    entries = []
    for seg in segments[1:]:
        header, _, body = seg.partition("\n")
        if header.startswith("TLSINTERNAL:"):
            continue
        if not header.startswith("NAME:"):
            continue
        issuer = not_after = subject = ""
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("subject="):
                subject = line[len("subject="):].strip()
            elif line.startswith("issuer="):
                issuer = line[len("issuer="):].strip()
            elif line.startswith("notAfter="):
                not_after = line[len("notAfter="):].strip()
        entries.append(
            (header[len("NAME:"):].strip(), issuer, not_after, subject))
    return entries


def _parse_tls_internal(stdout):
    """True when the instance's Caddy config asks for the internal CA.

    `tls internal` is a deliberate choice — typically behind a CDN that
    terminates public TLS — and it means Caddy never contacts ACME at
    all. Without this, an internally-issued certificate is indistinguishable
    from ACME having failed, and the check accuses a working instance.

    The directive may sit in the rendered Caddyfile (where Caddyfile.global
    is inlined) or in Caddyfile.site (imported into the site block at
    runtime), so both are searched.
    """
    for seg in stdout.split(_helpers._SENTINEL):
        header, _, _body = seg.partition("\n")
        if header.startswith("TLSINTERNAL:"):
            return bool(header[len("TLSINTERNAL:"):].strip())
    return False


def _parse_cert_manager(stdout):
    """True/False for the instance's ingress.certManager.enabled (the chart
    defaults it to false), or None when there is no values.yaml to read."""
    for seg in stdout.split(_helpers._SENTINEL):
        header, _, body = seg.partition("\n")
        if header.strip() != "VALUES:":
            continue
        try:
            values = yaml.safe_load(body) or {}
        except yaml.YAMLError:
            return None
        if not isinstance(values, dict):
            return None
        ingress = values.get("ingress") or {}
        cert_manager = ingress.get("certManager") or {}
        return bool(cert_manager.get("enabled", False))
    return None


def _parse_cert_expiry(text):
    """openssl's 'Sep 10 12:00:00 2026 GMT' -> aware datetime, or None."""
    raw = (text or "").strip()
    if raw.endswith(" GMT"):
        raw = raw[:-len(" GMT")]
    try:
        return datetime.datetime.strptime(
            raw, "%b %d %H:%M:%S %Y").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def _is_self_signed(issuer, subject):
    """True for a self-signed leaf (issuer == subject) or Traefik's built-in
    default certificate."""
    if issuer and issuer == subject:
        return True
    return any(m in issuer.lower() for m in _TLS_TRAEFIK_DEFAULT_MARKERS)


def _origin_tls_lines_from_entries(inst_id, entries, now,
                                   tls_internal=False, k8s=False,
                                   cert_manager=None, edge=False,
                                   internal_names=None):
    """doctor lines for the probed certificates. With `edge`, the HAProxy
    edge served them, and `tls_internal` means TLS_MODE=internal.
    internal_names maps each name the edge serves from its CA on purpose
    to the setting that asks for it."""
    ca = "the edge's internal CA" if edge else "Caddy internal CA"
    internal_names = internal_names or {}
    title = ["", "Origin TLS (%s):" % inst_id]
    if entries is None:
        return title + [
            "  SKIPPED — openssl is not installed on the host, so the served "
            "certificate cannot be inspected"]
    if not entries:
        return []
    lines = list(title)
    # Names whose certificate came from the internal CA without being
    # asked for. Collected and explained once: the diagnosis is identical
    # for every name, and repeating the paragraph per domain buries the
    # rest of the report.
    fallback_names = []
    for entry in entries:
        name, issuer, not_after = entry[:3]
        subject = entry[3] if len(entry) > 3 else ""
        if not issuer and not not_after:
            lines.append(
                "  %s: no certificate served on this host — skipped (nothing "
                "listening on the HTTPS port, or TLS terminates elsewhere)"
                % name)
            continue
        internal = any(
            m in issuer.lower() for m in _TLS_INTERNAL_ISSUER_MARKERS)
        if internal and not _is_public_hostname(name):
            # Internal-CA certificates last 12 hours, so the expiry checks
            # below would warn about a local name on every run.
            lines.append(
                "  %s: OK (%s — expected for a name with no "
                "public certificate)" % (name, ca))
            continue
        if internal and (tls_internal or name in internal_names):
            # `tls internal` is configured, so Caddy never contacts ACME.
            # An internal certificate is the requested outcome, not a
            # silent fallback.
            lines.append(
                "  %s: OK (%s — %s is configured for this instance)"
                % (name, ca, internal_names.get(name, "TLS_MODE=internal")
                   if edge else "'tls internal'"))
            continue
        if internal:
            fallback_names.append(name)
            continue
        if _is_self_signed(issuer, subject) and _is_public_hostname(name):
            if k8s and cert_manager is False:
                lines.append(
                    "  %s: no TLS certificate by design (cert-manager is not "
                    "enabled for this instance, e.g. created with "
                    "--skip-tls); the ingress controller's default "
                    "certificate is served (issuer %s)" % (name, issuer))
                continue
            if k8s:
                hint = (
                    "The Ingress is not getting a certificate for this host; "
                    "check the cert-manager Certificate and the Ingress TLS "
                    "secret in namespace %s."
                    % _helpers._k8s_namespace(inst_id))
            else:
                hint = "Check the tls directive in config/Caddyfile.site."
            lines.append(
                "  %s: WARN — self-signed certificate (issuer %s), not "
                "publicly trusted, so browsers reject it. %s"
                % (name, issuer, hint))
            continue
        expiry = _parse_cert_expiry(not_after)
        if expiry is None:
            lines.append(
                "  %s: issuer %s (expiry date unreadable: %r)"
                % (name, issuer or "unknown", not_after))
            continue
        days = (expiry - now).days
        if days < 0:
            lines.append(
                "  %s: WARN — certificate EXPIRED %d day(s) ago (issuer %s). "
                "Renewal has been failing; a CDN in a non-strict origin mode "
                "keeps serving visitors its own valid certificate, so nothing "
                "reports this until the CDN is set to strict origin "
                "validation, DNS changes, or someone reaches the origin "
                "directly." % (name, -days, issuer or "unknown"))
        elif days < _TLS_RENEWAL_FLOOR_DAYS:
            lines.append(
                "  %s: WARN — certificate expires in %d day(s) (issuer %s) "
                "and has not renewed. Renewal should have happened by now, so "
                "treat this as a broken ACME path rather than a countdown."
                % (name, days, issuer or "unknown"))
        elif any(m in issuer.lower()
                 for m in _TLS_STAGING_ISSUER_MARKERS):
            # Unexpired and correctly issued, so every check above passes
            # — but it chains to an untrusted root, so browsers reject it
            # exactly as they do the internal-CA case. Nothing in .env
            # distinguishes an instance created with --staging-certs from
            # one promoted afterwards; only the served certificate does.
            lines.append(
                "  %s: WARN — staging certificate (issuer %s), valid for %d "
                "more day(s) but not publicly trusted, so browsers reject "
                "it. Expected while testing with --staging-certs; on a live "
                "site, re-issue against the production CA by unsetting "
                "CANASTA_STAGING_CERTS and restarting."
                % (name, issuer, days))
        else:
            lines.append(
                "  %s: OK (expires in %d days, issuer %s)"
                % (name, days, issuer or "unknown"))
    if fallback_names and edge:
        lines.append(
            "  %s: WARN — served by the edge's internal CA, not a publicly "
            "trusted certificate. The edge has no Let's Encrypt certificate "
            "for these names. If the internal CA is what you want, set "
            "TLS_MODE=internal and this check will report it as "
            "intended." % ", ".join(fallback_names))
    elif fallback_names:
        lines.append(
            "  %s: WARN — served by Caddy's internal CA, not a publicly "
            "trusted certificate." % ", ".join(fallback_names))
        lines.append(
            "    ACME is failing and Caddy fell back silently; it reissues "
            "every 12 hours and logs \"certificate renewed successfully\" "
            "each time, so nothing else reports it. A CDN in a non-strict "
            "origin mode hides this from visitors, and switching that CDN to "
            "strict origin validation would take the site down. Check the "
            "caddy container's log for the underlying ACME failure. If the "
            "internal CA is what you want, set 'tls internal' in "
            "config/Caddyfile.site and this check will report it as "
            "intended.")
    return lines


# --- HAProxy edge -------------------------------------------------------------
#
# The edge's own containers, its membership list and acme.sh's certificates,
# read on the instance's host from the operator's config directory, as the
# edge module writes them.
_EDGE_SCRIPT = r"""
if [ "$(id -u)" = 0 ]; then d=/etc/canasta; else d="${XDG_CONFIG_HOME:-$HOME/.config}/canasta"; fi
printf '%%sEDGE:%%s\n' '%(delim)s' "$(%(inspect)s ps --filter 'name=^canasta-edge$' --format '{{.Status}}' 2>/dev/null)"
printf '%%sACME:%%s\n' '%(delim)s' "$(%(inspect)s ps --filter 'name=^canasta-edge-acme$' --format '{{.Status}}' 2>/dev/null)"
printf '%%sSPOA:%%s\n' '%(delim)s' "$(%(inspect)s ps --filter 'label=com.docker.compose.project=%(project)s' --filter 'label=com.docker.compose.service=crowdsec-spoa' --format '{{.Status}}' 2>/dev/null)"
printf '%%sDAEMON:%%s\n' '%(delim)s' "$(%(inspect)s info --format '{{json .SecurityOptions}}' 2>/dev/null)"
printf '%%sMEMBERS:\n' '%(delim)s'; cat "$d/edge/members.yaml" 2>/dev/null
printf '%%sHOSTS:\n' '%(delim)s'; cat "$d/edge/conf/hosts.map" 2>/dev/null
printf '%%sCERTS:\n' '%(delim)s'; %(inspect)s exec canasta-edge-acme acme.sh --list --listraw 2>/dev/null
true
"""


def _parse_edge_probe(stdout):
    """{'edge': status, 'acme': status, 'members': [ids], 'hosts': {id:
    [hostnames]}, 'certs': {host: (ca, renew)}} from the probe output."""
    probe = {"edge": "", "acme": "", "spoa": "", "daemon": "", "members": [],
             "hosts": {}, "certs": {}}
    for seg in stdout.split(_helpers._SENTINEL):
        header, _, body = seg.partition("\n")
        if header.startswith("EDGE:"):
            probe["edge"] = header[len("EDGE:"):].strip()
        elif header.startswith("ACME:"):
            probe["acme"] = header[len("ACME:"):].strip()
        elif header.startswith("SPOA:"):
            probe["spoa"] = header[len("SPOA:"):].strip()
        elif header.startswith("DAEMON:"):
            probe["daemon"] = header[len("DAEMON:"):].strip()
        elif header.startswith("MEMBERS:"):
            try:
                data = yaml.safe_load(body) or {}
            except yaml.YAMLError:
                data = {}
            probe["members"] = [m.get("id") for m in data.get("members") or []]
        elif header.startswith("HOSTS:"):
            # host@port instance/kind; "@port" lines are a port's instance
            # reached by localhost or an IP address, which has no certificate.
            for line in body.splitlines():
                fields = line.split()
                if len(fields) != 2 or fields[0].startswith("@"):
                    continue
                host, owner = fields[0].rsplit("@", 1)[0], fields[1].split("/", 1)[0]
                names = probe["hosts"].setdefault(owner, [])
                if host not in names:
                    names.append(host)
        elif header.startswith("CERTS:"):
            for line in body.splitlines()[1:]:
                fields = line.split("|")
                if len(fields) >= 7 and fields[0] and fields[5].strip():
                    probe["certs"][fields[0]] = (fields[4], fields[6].strip())
    return probe


def _edge_lines_from_probe(inst_id, tls_mode, names, probe, crowdsec=None,
                           plain=False, trusted=True, docker=True, rates_set=False,
                           host_modes=None):
    """doctor lines for an instance behind the HAProxy edge. crowdsec is
    None when CrowdSec is off, else whether its edge bouncer is enrolled.
    plain is an instance served over plain HTTP; trusted, whether
    TRUSTED_PROXIES names the proxy in front of it. host_modes holds the
    TLS_MODE_HOSTS entries, which override tls_mode for their names."""
    lines = ["", "HAProxy edge (%s):" % inst_id]
    if probe["edge"]:
        lines.append("  edge: running (%s)" % probe["edge"])
    else:
        lines.append(
            "  edge: WARN — not running, so this instance's sites are not "
            "served. Run 'canasta start -i %s' to start it." % inst_id)
    if inst_id in probe["members"]:
        lines.append("  member: yes")
    else:
        lines.append(
            "  member: WARN — not among the edge's instances. Run 'canasta "
            "restart -i %s' to add it." % inst_id)
    if crowdsec is not None:
        lines.append(_edge_crowdsec_line(inst_id, crowdsec, probe["spoa"]))
    # Rootless Podman reports rootless too, but its edge keeps addresses.
    if docker and "name=rootless" in probe["daemon"]:
        lines.append(
            "  client addresses: WARN — this instance runs on rootless Docker, where "
            "visitors' IP addresses are lost unless the daemon was set up with "
            "'canasta install docker-rootless'. MediaWiki, CrowdSec and the edge then "
            "see every visitor as the same address.%s"
            % ("" if rates_set else " The edge applies no per-client rate limits "
               "until you set CANASTA_EDGE_RATE_DYNAMIC and CANASTA_EDGE_RATE_DEFAULT."))
    if plain:
        if not trusted:
            lines.append(
                "  client addresses: WARN — this instance is served over plain HTTP "
                "behind its own proxy, which TRUSTED_PROXIES does not name, so "
                "MediaWiki and the edge see only that proxy's address and the edge's "
                "per-client rate limits are off. Set TRUSTED_PROXIES to the "
                "proxy's addresses.")
        return lines
    acme_names = [n for n in names if (host_modes or {}).get(n, tls_mode)
                  in canasta_edge_render.ACME_MODES]
    if not acme_names:
        return lines
    if probe["acme"]:
        lines.append("  acme.sh: running (%s)" % probe["acme"])
    else:
        lines.append(
            "  acme.sh: WARN — not running, so Let's Encrypt certificates "
            "are not renewed. Run 'canasta start -i %s' to start it." % inst_id)
    for name in acme_names:
        cert = probe["certs"].get(name)
        if cert:
            ca = "staging" if cert[0].endswith("_test") else "production"
            lines.append("  %s: Let's Encrypt %s, renews %s" % (name, ca, cert[1]))
        else:
            lines.append(
                "  %s: WARN — no Let's Encrypt certificate yet; the edge serves "
                "its own CA's, which browsers reject. It is requested again at "
                "each start." % name)
    return lines


def _edge_crowdsec_line(inst_id, enrolled, status):
    if not enrolled:
        return ("  CrowdSec bouncer: WARN — not enrolled yet, so only Caddy's "
                "bouncer refuses banned clients. Run 'canasta restart -i %s' "
                "to enroll it." % inst_id)
    if status:
        return "  CrowdSec bouncer: running (%s)" % status
    return ("  CrowdSec bouncer: WARN — not running, so the edge serves this "
            "instance's clients without asking CrowdSec; Caddy's bouncer still "
            "refuses banned ones. Run 'canasta restart -i %s' to start it." % inst_id)


def _edge_lines(inst):
    """doctor lines for a Compose instance's place behind the edge; [] on
    Kubernetes."""
    path = inst.get("path", "")
    if not path or _helpers._is_k8s(inst):
        return []
    host = inst.get("host") or "localhost"
    inspect = inst.get("inspectCommand") or "docker"
    project = os.path.basename(path.rstrip("/")).lower()
    script = _EDGE_SCRIPT % {"delim": _helpers._SENTINEL,
                             "inspect": _helpers._shell_quote(inspect),
                             "project": project}
    docker_host = inst.get("dockerHost")
    if docker_host:
        script = "export DOCKER_HOST=%s\n%s" % (_helpers._shell_quote(docker_host), script)
    try:
        if _helpers._is_localhost(host):
            stdout = subprocess.run(["bash", "-c", script], capture_output=True,
                                    text=True, timeout=60).stdout
        else:
            _, stdout = _helpers._ssh_run(host, script)
    except (subprocess.TimeoutExpired, OSError):
        return []
    tls_mode = (_helpers._read_env_for(inst, "TLS_MODE") or "acme").strip().lower()
    plain = tls_mode == "off"
    host_modes = _tls_mode_hosts(inst)
    crowdsec = None
    if (_helpers._read_env_for(inst, "CANASTA_ENABLE_CROWDSEC") or "").strip().lower() == "true":
        crowdsec = bool((_helpers._read_env_for(inst, "CROWDSEC_SPOA_API_KEY") or "").strip())
    trusted = bool((_helpers._read_env_for(inst, "TRUSTED_PROXIES") or "").strip())
    rates_set = any((_helpers._read_env_for(inst, k) or "").strip()
                    for k in ("CANASTA_EDGE_RATE_DYNAMIC", "CANASTA_EDGE_RATE_DEFAULT"))
    probe = _parse_edge_probe(stdout)
    # The edge's own map holds every name it serves the instance under:
    # wiki and media hostnames and Caddyfile.global sites.
    names = probe["hosts"].get(inst.get("id")) or [
        n for n in _origin_tls_server_names(path, host)
        if n != "localhost" and not n.endswith(".localhost")
        and not re.match(r"^[0-9.]+$", n)]
    return _edge_lines_from_probe(inst.get("id", "?"), tls_mode, names, probe,
                                  crowdsec, plain, trusted,
                                  "podman" not in os.path.basename(inspect), rates_set,
                                  host_modes)


def _tls_mode_hosts(inst):
    """The instance's TLS_MODE_HOSTS as {hostname: mode}; {} when unset or
    unreadable (the edge itself reports an unreadable value)."""
    try:
        return canasta_edge_render.parse_tls_mode_hosts(
            _helpers._read_env_for(inst, "TLS_MODE_HOSTS"), inst.get("id", "?"))
    except canasta_edge_render.EdgeError:
        return {}


def _caddy_tls_lines(inst):
    """doctor lines for tls directives in a Compose instance's
    Caddyfile.site and Caddyfile.global; [] when it has none, and on
    Kubernetes."""
    if not inst.get("path") or _helpers._is_k8s(inst):
        return []
    found = _helpers._caddy_tls_directives(inst)
    if not found:
        return []
    return ["", "Caddy configuration (%s):" % inst.get("id", "?"),
            "  tls directives: WARN — the instance %s." % canasta_caddyfile.tls_directives_problem(found)]


def _origin_tls_server_names(path, host):
    """Unique hostnames the instance serves, derived from wikis.yaml as
    rewrite_caddy.yml derives the Caddy site address."""
    names = []
    for wiki in _helpers._read_wikis(path, host):
        url = (wiki.get("url") or "").strip()
        if not url:
            continue
        name = re.sub(r":[0-9]+$", "", url.split("/")[0])
        if name and name not in names:
            names.append(name)
    return names


_INNODB_QUERY = (
    "SELECT @@version_comment, @@innodb_buffer_pool_size, "
    "(SELECT IFNULL(SUM(data_length + index_length), 0) "
    "FROM information_schema.tables WHERE engine = 'InnoDB')"
)


def _human_bytes(n):
    """Bytes as the operator would say them: 128 MB, 1.0 GB."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return ("%d %s" % (value, unit) if unit in ("B", "KB", "MB")
                    else "%.1f %s" % (value, unit))
        value /= 1024
    return "%d B" % n


def _db_tuning_lines(inst):
    """doctor lines for the database's buffer pool, or [] when the check
    does not apply.

    Reports the value the *server* is running with, not what a file says:
    my.cnf is read only at server start, so an edit that has not been
    followed by a restart leaves the two disagreeing.
    """
    if not inst:
        return []
    # Kubernetes ships its own my.cnf through the chart and the tuning
    # path differs; Compose is where the instance-directory file applies.
    if _helpers._is_k8s(inst):
        return []
    path = inst.get("path", "")
    if not path:
        return []
    host = inst.get("host") or "localhost"
    # An external database is not ours to report on: Canasta neither
    # ships its config nor restarts it.
    if (_helpers._read_env_for(inst, "USE_EXTERNAL_DB")
            or "false").strip().lower() == "true":
        return []
    compose_cmd = _helpers._resolve_compose_cmd(inst)
    argv = (list(compose_cmd) + _helpers._compose_profile_args(inst)
            + ["exec", "-T", "db", "sh", "-c",
               'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mariadb -u root -N -B -e '
               + _helpers._shell_quote(_INNODB_QUERY)])
    out = _helpers._capture_in_instance(
        path, host, inst.get("dockerHost"), argv, capture_stderr=True)
    if out is None:
        return []
    # A my.cnf setting under [client] breaks the client tools rather than
    # merely being ignored by the server: [client] is a group they DO
    # read, so an unknown variable there makes every mariadb and
    # mariadb-dump invocation fail — backups and restores included. The
    # server is unaffected and keeps serving, so nothing else reports it.
    if "unknown variable" in out.lower():
        bad = out.strip().split("\n")[-1].strip()
        return [
            "", "Database tuning (%s):" % inst.get("id", "?"),
            "  WARN — the database client cannot start: %s" % bad,
            "  A server setting placed under my.cnf's [client] header does "
            "this: the client tools read that group and reject options they "
            "do not know. Backups, restores and maintenance queries all use "
            "those tools. Move the setting to a [mysqld] section.",
        ]
    if not out.strip():
        return []
    fields = out.strip().split("\n")[-1].split("\t")
    if len(fields) < 3:
        return []
    version_comment, pool_raw, data_raw = fields[0], fields[1], fields[2]
    # The check speaks MariaDB's dialect and defaults; say nothing rather
    # than guess if the server is something else.
    if "mariadb" not in version_comment.lower():
        return []
    try:
        pool, data = int(pool_raw), int(data_raw)
    except ValueError:
        return []

    lines = ["", "Database tuning (%s):" % inst.get("id", "?")]
    detail = "  innodb_buffer_pool_size: %s" % _human_bytes(pool)
    if pool == 134217728:
        detail += " (MariaDB's compiled default)"
    lines.append(detail)
    lines.append("  InnoDB data on disk:     %s" % _human_bytes(data))

    # my.cnf is the hook operators are told to use, and a value there that
    # the server is not running is the failure the docs warn about: the
    # file is read at start, so an edit without a restart changes nothing.
    configured = _my_cnf_buffer_pool(path, host, inst)
    if configured is not None and configured != pool:
        lines.append(
            "  WARN — my.cnf sets %s but the server is running %s. my.cnf is "
            "read at server start: run 'canasta reconcile', which recreates "
            "only the database, or 'canasta restart' to apply it."
            % (_human_bytes(configured), _human_bytes(pool)))
    elif pool == 134217728 and data > pool:
        lines.append(
            "  The buffer pool is smaller than the data it caches. InnoDB "
            "uses O_DIRECT, so the pool is its only cache and free host RAM "
            "does not help. See Help:Best practices for how to size and set "
            "it.")
    return lines


def _my_cnf_buffer_pool(path, host, inst):
    """innodb_buffer_pool_size as my.cnf sets it, in bytes, or None.

    Only a [mysqld]-section setting counts: the shipped file's [client]
    header is a group the server never reads, so a value under it is
    inert and must not be reported as configured.
    """
    out = _helpers._capture_in_instance(
        path, host, inst.get("dockerHost"),
        ["cat", os.path.join(path, "my.cnf")])
    if not out:
        return None
    group = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            group = stripped[1:-1].strip().lower()
            continue
        if group not in ("mysqld", "server", "mariadb", "mariadbd"):
            continue
        match = re.match(
            r"innodb_buffer_pool_size\s*=\s*(\d+)\s*([kmgt]?)b?\s*$",
            stripped, re.I)
        if match:
            scale = {"": 1, "k": 1024, "m": 1024 ** 2,
                     "g": 1024 ** 3, "t": 1024 ** 4}[match.group(2).lower()]
            return int(match.group(1)) * scale
    return None


def _origin_tls_lines(inst):
    """doctor lines for the certificate the instance actually serves, or []
    when the check does not apply (no path/names, plain-HTTP Compose)."""
    path = inst.get("path", "")
    if not path:
        return []
    host = inst.get("host") or "localhost"
    is_k8s = _helpers._is_k8s(inst)
    # The ingress controller, or the HAProxy edge on the instance's own
    # HTTPS port, terminates TLS.
    edge = not is_k8s
    if is_k8s:
        port = "443"
    else:
        if (_helpers._read_env_for(inst, "TLS_MODE") or "").strip().lower() == "off":
            return []
        port = _helpers._read_env_for(inst, "HTTPS_PORT") or "443"
    names = _origin_tls_server_names(path, host)
    if not names:
        return []
    script = _ORIGIN_TLS_SCRIPT % {
        "names": " ".join(_helpers._shell_quote(n) for n in names),
        "port": _helpers._shell_quote(str(port)),
        "delim": _helpers._SENTINEL,
        "cfg": " ".join(
            _helpers._shell_quote(os.path.join(path, "config", f))
            for f in ("Caddyfile", "Caddyfile.site")),
        "values": _helpers._shell_quote(os.path.join(path, "values.yaml")),
    }
    try:
        if _helpers._is_localhost(host):
            stdout = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=60,
            ).stdout
        else:
            rc, stdout = _helpers._ssh_run(host, script)
            if rc != 0 and not stdout.strip():
                return []
    except (subprocess.TimeoutExpired, OSError):
        return []
    if not stdout.strip():
        return []
    internal_names = {}
    if edge:
        host_modes = _tls_mode_hosts(inst)
        default = (_helpers._read_env_for(inst, "TLS_MODE") or "acme").strip().lower()
        internal_names = {
            n: ("TLS_MODE_HOSTS=%s=internal" % n if n in host_modes
                else "TLS_MODE=internal")
            for n in names if host_modes.get(n, default) == "internal"}
        tls_internal = False
    else:
        tls_internal = _parse_tls_internal(stdout)
    return _origin_tls_lines_from_entries(
        inst.get("id", "?"), _parse_origin_tls(stdout),
        datetime.datetime.now(datetime.timezone.utc),
        tls_internal=tls_internal, k8s=is_k8s,
        cert_manager=_parse_cert_manager(stdout) if is_k8s else None,
        edge=edge, internal_names=internal_names)


# --- Kubernetes config drift ------------------------------------------------
#
# On K8s, "desired" config is the operator's local files and "actual" is what
# was last deployed — captured on disk as values-configdata.yaml (written by
# k8s_sync_config.yml on every start/restart/reconcile). We compare current
# managed config files against that snapshot; a difference means edits that a
# `canasta reconcile` would apply. Managed config == exactly what the sync
# task deploys, keyed the same way so the comparison is apples-to-apples:
#   - configData.web:      .env (secrets stripped), wikis.yaml,
#                          composer.local.json, settings--<encoded path>
#   - configData.caddy:    Caddyfile, Caddyfile.site
#   - configData.varnish:  default.vcl
#   - configData.crowdsec: acquis.yaml, whitelists.yaml (CrowdSec enabled)
# Extensions/skins (PVC + boot-time symlinks, need a restart) and secrets are
# intentionally out of scope; gitops-managed instances are served by
# `canasta gitops status`/`diff` instead.

# Each managed file's path relative to the instance dir -> (channel, key) in
# the configData snapshot. Settings files are handled separately (dynamic).
_K8S_STATIC_CONFIG_MAP = {
    ".env": ("web", ".env"),
    "config/wikis.yaml": ("web", "wikis.yaml"),
    "config/composer.local.json": ("web", "composer.local.json"),
    "config/Caddyfile": ("caddy", "Caddyfile"),
    "config/Caddyfile.site": ("caddy", "Caddyfile.site"),
    "config/default.vcl": ("varnish", "default.vcl"),
    "config/crowdsec/acquis.yaml": ("crowdsec", "acquis.yaml"),
    "config/crowdsec/whitelists.yaml": ("crowdsec", "whitelists.yaml"),
}

# Fetch, on the host where the instance dir lives: the gitops marker, the
# deployed snapshot, and the current content of every managed config file.
# Files are emitted as `<delim>FILE:<relpath>\n<content>` so content can hold
# anything without breaking the framing.
_K8S_DRIFT_SCRIPT = r"""
cd '%(path)s' 2>/dev/null || exit 0
[ -f .gitops-host ] && echo GITOPS_YES || echo GITOPS_NO
printf '%%s\n' '%(delim)s'
cat values-configdata.yaml 2>/dev/null
for f in .env config/wikis.yaml config/composer.local.json config/Caddyfile \
         config/Caddyfile.site config/default.vcl \
         config/crowdsec/acquis.yaml config/crowdsec/whitelists.yaml; do
  [ -f "$f" ] && { printf '%%sFILE:%%s\n' '%(delim)s' "$f"; cat "$f"; }
done
if [ -d config/settings ]; then
  find config/settings -type f 2>/dev/null | while IFS= read -r f; do
    [ "$(basename "$f")" = README ] && continue
    printf '%%sFILE:%%s\n' '%(delim)s' "$f"; cat "$f"
  done
fi
printf '%%sEND\n' '%(delim)s'
"""


def _k8s_settings_key(relpath):
    """config/settings/global/Foo.php -> settings--global--Foo.php, matching
    k8s_sync_config.yml's ConfigMap-key encoding."""
    return "settings--" + relpath[len("config/settings/"):].replace("/", "--")


def _k8s_strip_env_secrets(text):
    """Drop the secret-bearing lines k8s_sync_config.yml strips before it
    embeds .env in the web ConfigMap, using the same classification, so a
    change to a secret is not reported as ConfigMap drift."""
    return [ln for ln in text.splitlines()
            if "=" not in ln or not _helpers._is_secret_key(ln.split("=", 1)[0])]


def _k8s_display_path(channel, key):
    """Friendly instance-relative path for a (channel, key) pair, for output."""
    if channel == "web":
        if key.startswith("settings--"):
            return "config/settings/" + key[len("settings--"):].replace(
                "--", "/")
        return key if key == ".env" else "config/" + key
    if channel == "crowdsec":
        return "config/crowdsec/" + key
    return "config/" + key


def _k8s_current_channels(current_files):
    """Map {relpath: content} of current managed files into the same
    {channel: {key: content}} shape as the deployed snapshot's configData."""
    channels = {"web": {}, "caddy": {}, "varnish": {}, "crowdsec": {}}
    for relpath, content in current_files.items():
        if relpath.startswith("config/settings/"):
            channels["web"][_k8s_settings_key(relpath)] = content
        elif relpath in _K8S_STATIC_CONFIG_MAP:
            channel, key = _K8S_STATIC_CONFIG_MAP[relpath]
            if relpath == ".env":
                # Compare against the secret-stripped snapshot form.
                content = "\n".join(_k8s_strip_env_secrets(content)) + "\n"
            channels[channel][key] = content
    # The sync deploys the crowdsec channel only when CrowdSec is enabled.
    env = {k: v for k, v, c in
           _helpers._parse_env_entries(current_files.get(".env", ""))
           if not c and k}
    if env.get("CANASTA_ENABLE_CROWDSEC", "").strip().lower() != "true":
        channels["crowdsec"] = {}
    return channels


def _k8s_wikis_yaml_as_served(text):
    """wikis.yaml without what only the CLI reads (each wiki's
    extra_databases, which feed backups from the host copy), so declaring a
    backup database is not reported as config the pods are missing. Text
    that does not parse is returned unchanged."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    if isinstance(data, dict) and isinstance(data.get("wikis"), list):
        data = dict(data, wikis=[
            {k: v for k, v in w.items() if k != "extra_databases"}
            if isinstance(w, dict) else w
            for w in data["wikis"]])
    return data


def _k8s_config_drift(baseline_channels, current_channels):
    """Return [(display_path, status)] where status is added/changed/removed,
    comparing the deployed snapshot's configData channels against current
    files. Trailing-newline differences are ignored (YAML round-trips them)."""
    drift = []
    for channel in ("web", "caddy", "varnish", "crowdsec"):
        base = baseline_channels.get(channel) or {}
        cur = current_channels.get(channel) or {}
        for key in sorted(set(base) | set(cur)):
            disp = _k8s_display_path(channel, key)
            if key not in base:
                drift.append((disp, "added"))
            elif key not in cur:
                drift.append((disp, "removed"))
            elif channel == "web" and key == "wikis.yaml":
                if (_k8s_wikis_yaml_as_served(str(base[key]))
                        != _k8s_wikis_yaml_as_served(str(cur[key]))):
                    drift.append((disp, "changed"))
            elif str(base[key]).rstrip("\n") != str(cur[key]).rstrip("\n"):
                drift.append((disp, "changed"))
    return drift


def _parse_k8s_drift_payload(stdout):
    """Split the drift script output into (is_gitops, baseline_yaml_text,
    {relpath: content})."""
    segments = stdout.split(_helpers._SENTINEL)
    is_gitops = segments[0].strip() == "GITOPS_YES"
    baseline_text = segments[1] if len(segments) > 1 else ""
    current_files = {}
    for seg in segments[2:]:
        if not seg.startswith("FILE:"):
            continue
        header, _, content = seg.partition("\n")
        relpath = header[len("FILE:"):].strip()
        if relpath:
            current_files[relpath] = content
    return is_gitops, baseline_text, current_files


def _k8s_drift_lines_from_payload(inst_id, is_gitops, baseline_text,
                                  current_files):
    """Build the doctor output lines from an already-fetched payload (pure —
    no I/O, so it is the unit-testable core)."""
    title = ["", "Config sync (%s):" % inst_id]
    if is_gitops:
        return title + [
            "  Managed via gitops — check for unapplied edits with "
            "'canasta gitops status' or 'canasta gitops diff'."]
    import yaml
    try:
        snapshot = yaml.safe_load(baseline_text) or {}
    except yaml.YAMLError:
        snapshot = {}
    baseline_channels = snapshot.get("configData")
    if not baseline_channels:
        return title + [
            "  No deployed config snapshot yet — run 'canasta reconcile' or "
            "'canasta restart' once to enable drift detection."]
    drift = _k8s_config_drift(
        baseline_channels, _k8s_current_channels(current_files))
    if not drift:
        return title + ["  OK (deployed config matches local config)"]
    lines = title + [
        "  WARN: local config edits not yet applied to the running instance:"]
    lines += ["    - %s (%s)" % (disp, status) for disp, status in drift]
    lines.append("  Run 'canasta reconcile' to apply them.")
    return lines


def _k8s_config_consistency_lines(inst):
    """doctor lines for a K8s instance's local-config-vs-deployed drift, or []
    when it can't be determined (no path, host unreachable)."""
    path = inst.get("path", "")
    if not path:
        return []
    host = inst.get("host") or "localhost"
    script = _K8S_DRIFT_SCRIPT % {"path": path, "delim": _helpers._SENTINEL}
    try:
        if _helpers._is_localhost(host):
            stdout = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30,
            ).stdout
        else:
            rc, stdout = _helpers._ssh_run(host, script)
            if rc != 0 and not stdout.strip():
                return []
    except (subprocess.TimeoutExpired, OSError):
        return []
    if not stdout.strip():
        return []
    is_gitops, baseline_text, current_files = _parse_k8s_drift_payload(stdout)
    return _k8s_drift_lines_from_payload(
        inst.get("id", "?"), is_gitops, baseline_text, current_files)


def _composer_failure_lines(inst):
    """doctor lines for a failed startup composer update, or [] when there
    is none or the web container cannot be read."""
    inst_id = inst.get("id", "?")
    summary = _helpers._composer_failure_summary(inst_id, inst)
    if not summary:
        return []
    return [
        "", "Composer dependencies (%s):" % inst_id,
        "  WARN — %s The packages in config/composer.local.json are not "
        "installed; the wiki runs on the image's bundled vendor/. Fix the "
        "cause, then run 'canasta restart' to retry." % summary,
        "  %s" % _helpers._composer_failure_details_hint(inst_id),
    ]


_WEB_INSPECT_FORMAT = (
    "{{.State.Status}}|{{.RestartCount}}|"
    "{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.ExitCode}}")

# How much of web's log to show when no startup error is recognized in it.
_WEB_LOG_TAIL_LINES = 10


def _restarts(n):
    return "%d restart%s" % (n, "" if n == 1 else "s")


def _compose_web_problem(inst):
    """(description, web log text, startup cause) for an unhealthy Compose
    web container, or None when it is healthy or not running."""
    cid = _helpers._web_container_id(inst)
    if not cid:
        return None
    rc, out = _helpers._runtime_capture(inst, [
        _helpers._resolve_inspect_cmd(inst), "inspect", "-f",
        _WEB_INSPECT_FORMAT, cid,
    ])
    if rc != 0:
        return None
    fields = out.strip().split("|") + [""] * 4
    state, health, exit_code = fields[0], fields[2], fields[3]
    try:
        restarts = int(fields[1])
    except ValueError:
        restarts = 0
    if state == "restarting":
        what = ("is crash-looping (last exit code %s, %s), so the wiki is "
                "not being served" % (exit_code or "?", _restarts(restarts)))
    elif health == "unhealthy":
        what = ("is running but failing its health check (%s), so the wiki "
                "may not be served" % _restarts(restarts))
    elif health == "starting" and restarts > 0:
        # Between crashes a crash-looping container runs again for a while.
        what = ("is starting again after %s, so the wiki may not be served"
                % _restarts(restarts))
    else:
        return None
    cause, logs = _helpers._compose_web_startup_cause(
        inst.get("id", "?"), inst)
    return what, logs, cause


def _k8s_web_problem(inst):
    """(description, web log text, startup cause) for an instance with a
    crash-looping web pod, or None when there is none."""
    inst_id = inst.get("id")
    if not inst_id:
        return None
    host = inst.get("host") or "localhost"
    rc, out = _helpers._k8s_web_pods_query(inst_id, host)
    if rc != 0:
        return None
    bad = [p for p in _helpers._k8s_web_pods(out)
           if _helpers._k8s_pod_unhealthy(p)]
    if not bad:
        return None
    pod = bad[0]
    what = "pod %s is %s (last exit code %s, %s)" % (
        pod["name"],
        "crash-looping" if pod["reason"] == "CrashLoopBackOff"
        else "not ready",
        pod["exit_code"] or "?", _restarts(pod["restarts"]))
    served = any(p["ready"] for p in _helpers._k8s_web_pods(out))
    what += (", so the wiki may not be served" if served
             else ", so the wiki is not being served")
    # A crash-looping container's own error is in its previous run's log.
    ns = _helpers._shell_quote("canasta-%s" % inst_id)
    script = (
        "kubectl logs -n %(ns)s -l app.kubernetes.io/component=web -c web "
        "--previous --tail=%(n)d 2>/dev/null; "
        "kubectl logs -n %(ns)s -l app.kubernetes.io/component=web -c web "
        "--tail=%(n)d 2>/dev/null; true"
    ) % {"ns": ns, "n": _helpers._WEB_CAUSE_SCAN_LINES}
    if _helpers._is_localhost(host):
        try:
            logs = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except (subprocess.TimeoutExpired, OSError):
            logs = ""
    else:
        _rc, logs = _helpers._ssh_run(host, script)
    cause = canasta_web_cause.web_startup_cause(logs, inst_id)
    return what, logs, cause


JOB_BACKLOG_KEY = "CANASTA_DOCTOR_JOB_BACKLOG"
JOB_BACKLOG_DEFAULT = 10000
_JOB_TYPES_SHOWN = 3
# `showJobs --group` prints one line per job type, such as
# "refreshLinks: 12 queued; 0 claimed (0 active, 0 abandoned); 0 delayed".
_JOB_GROUP_LINE = re.compile(r"^(\S+): (\d+) queued")
_WIKI_MARK = "@@wiki "


def _job_backlog_threshold(inst):
    raw = (_helpers._read_env_for(inst, JOB_BACKLOG_KEY) or "").strip()
    try:
        return max(int(raw), 0) if raw else JOB_BACKLOG_DEFAULT
    except ValueError:
        return JOB_BACKLOG_DEFAULT


def _parse_job_groups(out):
    """{wiki: [(job type, queued)]} from the per-wiki showJobs output."""
    queues = {}
    wiki = None
    for line in (out or "").splitlines():
        if line.startswith(_WIKI_MARK):
            wiki = line[len(_WIKI_MARK):].strip()
            queues[wiki] = []
            continue
        m = _JOB_GROUP_LINE.match(line.strip())
        if wiki is not None and m:
            queues[wiki].append((m.group(1), int(m.group(2))))
    return queues


def _job_backlog_warnings(queues, threshold):
    """One line per wiki whose queue is above threshold, largest first."""
    lines = []
    totals = {w: sum(n for _, n in groups) for w, groups in queues.items()}
    for wiki in sorted(totals, key=lambda w: -totals[w]):
        if totals[wiki] <= threshold:
            continue
        top = sorted(queues[wiki], key=lambda g: -g[1])[:_JOB_TYPES_SHOWN]
        lines.append("  WARN — %s: %s jobs queued (%s)" % (
            wiki, "{:,}".format(totals[wiki]),
            ", ".join("%s %s" % (t, "{:,}".format(n)) for t, n in top)))
    return lines


def _job_backlog_lines(inst):
    """doctor lines for wikis whose job queue is above the threshold, or []
    when none is, or the web container cannot be read."""
    wikis = [w.get("id") for w in _helpers._read_wikis(
        inst.get("path", ""), inst.get("host") or "localhost") if w.get("id")]
    if not wikis:
        return []
    script = ('for w in %s; do echo "%s$w"; php maintenance/run.php '
              'showJobs --wiki="$w" --group 2>/dev/null; done'
              % (" ".join(shlex.quote(w) for w in wikis), _WIKI_MARK))
    rc, out = _helpers._exec_in_container(
        inst.get("id", "?"), inst, script, user="www-data", timeout=120)
    if rc != 0:
        return []
    threshold = _job_backlog_threshold(inst)
    warnings = _job_backlog_warnings(_parse_job_groups(out), threshold)
    if not warnings:
        return []
    return ["", "Job queues (%s):" % inst.get("id", "?")] + warnings + [
        "  Above %s jobs (%s). Check that the job runner is enabled "
        "(MW_ENABLE_JOB_RUNNER) and not failing on one job type." % (
            "{:,}".format(threshold), JOB_BACKLOG_KEY)]


def _web_health_lines(inst):
    """doctor lines for a web container that is crash-looping or failing
    its health check, or [] when it is healthy, stopped or unreadable."""
    if _helpers._is_k8s(inst):
        problem = _k8s_web_problem(inst)
    else:
        problem = _compose_web_problem(inst)
    if not problem:
        return []
    what, logs, cause = problem
    lines = [
        "", "Web container (%s):" % inst.get("id", "?"),
        "  WARN — web %s." % what,
    ]
    if cause:
        lines.append("  Cause: %s" % cause["cause"])
        lines.append("  Fix: %s" % cause["hint"])
        return lines
    tail = (logs or "").strip().splitlines()[-_WEB_LOG_TAIL_LINES:]
    if not tail:
        lines.append("  Its log is empty or could not be read.")
        return lines
    lines.append(
        "  No known startup error was found in its log. Its last %d lines:"
        % len(tail))
    lines += ["    %s" % line for line in tail]
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
        # Match _resolve_instance_by_cwd: target this instance's rootless
        # socket so the runtime consistency checks below see its containers.
        docker_host = inst.get("dockerHost")
        if docker_host:
            os.environ["DOCKER_HOST"] = docker_host
    else:
        _, inst = _helpers._resolve_instance_by_cwd(args)
    if not host and inst:
        host = inst.get("host") or "localhost"
    script = _DOCTOR_SCRIPT % {"delim": _helpers._SENTINEL,
                               "cmd_timeout": PROBE_COMMAND_TIMEOUT}

    # Under canasta-docker a local probe runs inside the CLI container,
    # not on the host.
    docker_mode = False
    probe_timed_out = False
    if not host or host == "localhost":
        hostname = "localhost"
        docker_mode = os.environ.get("CANASTA_RUN_MODE") == "docker"
        try:
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            )
            stdout = result.stdout
        except subprocess.TimeoutExpired as e:
            # Captured output arrives as bytes even with text=True.
            partial = e.stdout or b""
            stdout = partial.decode(errors="replace") if isinstance(partial, bytes) else partial
            probe_timed_out = True
        except OSError as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1
    else:
        hostname = host
        rc, stdout = _helpers._ssh_run(host, script, timeout=PROBE_TIMEOUT)
        if rc != 0 and not stdout.strip():
            print("Error: failed to connect to %s" % host, file=sys.stderr)
            return 1

    print(_parse_doctor(stdout, hostname, docker_mode))
    if probe_timed_out:
        print("\nHost checks: FAIL — they did not finish within %d seconds; "
              "those not reached show as unknown or missing." % PROBE_TIMEOUT)

    for line in _collection_integrity_lines():
        print(line)

    for line in _instance_consistency_lines(inst):
        print(line)

    if inst:
        for line in _web_health_lines(inst):
            print(line)

        for line in _db_tuning_lines(inst):
            print(line)

        for line in _origin_tls_lines(inst):
            print(line)

        for line in _edge_lines(inst):
            print(line)

        for line in _caddy_tls_lines(inst):
            print(line)

        for line in _composer_failure_lines(inst):
            print(line)

        for line in _job_backlog_lines(inst):
            print(line)

    if probe_timed_out:
        return 1

    parts = stdout.split(_helpers._SENTINEL + "\n")
    docker = parts[1].strip() if len(parts) > 1 else "MISSING"
    compose = parts[2].strip() if len(parts) > 2 else "MISSING"
    daemon = parts[3].strip() if len(parts) > 3 else "NOT_RUNNING"
    podman_version = parts[22].strip() if len(parts) > 22 else "MISSING"
    if not _has_container_runtime(docker, compose, daemon, podman_version):
        print("\nMissing core dependencies. Install Docker or Podman and ensure a runtime is running.",
              file=sys.stderr)
        return 1

    return 0
