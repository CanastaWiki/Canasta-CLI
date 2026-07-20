"""doctor command — dependency checks."""

import os
import re
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
cdir="$(dirname "$(readlink -f "$(command -v canasta 2>/dev/null)" 2>/dev/null)" 2>/dev/null)"; if [ -n "$cdir" ] && [ -d "$cdir/.git" ]; then { [ -w "$cdir/.git" ] && echo WRITABLE || echo NOT_WRITABLE; } else echo NA; fi; echo "$D"
command -v sops >/dev/null 2>&1 && echo OK || echo MISSING; echo "$D"
command -v age-keygen >/dev/null 2>&1 && echo OK || echo MISSING; echo "$D"
podman --version 2>&1 || echo MISSING
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
    # SOPS toolchain for encrypted K8s gitops secrets (appended, so it didn't
    # shift the positional indices above): sops on the gitops host, age on the
    # controller.
    sops = p(20) if len(parts) > 20 else "MISSING"
    age = p(21) if len(parts) > 21 else "MISSING"
    podman_version = p(22) if len(parts) > 22 else "MISSING"

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

    if podman_version != "MISSING":
        ver = podman_version
        # Parse "podman version 4.3.1" → "4.3.1"
        m = re.search(r'(\d+\.\d+\.\d+)', ver)
        if m:
            ver_str = m.group(1)
            parts = [int(x) for x in ver_str.split(".")]
            # Pad to 3 components for comparison
            while len(parts) < 3:
                parts.append(0)
            if parts < [4, 4, 0]:
                lines.append(
                    "  Podman:          %s — WARNING: podman < 4.4 has a "
                    "known Docker API bug that causes 'docker compose up' to "
                    "fail with EOF on container start. "
                    "Install podman >= 4.4 or set compose_command=podman-compose "
                    "in the instance .env."
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


def _consistency_warnings(env, current_profiles, running_services, uses_cirrus,
                          template_literals=None):
    """Pure: warnings about config/runtime drift for a Compose instance.

    - COMPOSE_PROFILES that disagrees with what sync would derive from the
      flags + DB mode.
    - A container running under a profile that isn't active (unmanaged — a
      stop/start won't restore it).
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

    drifted = sorted(
        k for k, tv in (template_literals or {}).items()
        if k in env and env[k] != tv)
    if drifted:
        detail = "; ".join(
            "%s (env.template=%s, .env=%s)" % (k, template_literals[k], env[k])
            for k in drifted)
        warns.append(
            "env.template disagrees with .env on: %s. A gitops pull or "
            "'canasta config regenerate' re-renders .env from env.template, so "
            "these values will change: regenerate if the template is right, or "
            "update env.template/vars.yaml if the .env value is intended"
            % detail)

    return warns


def _gather_runtime(path, host):
    """(running_services, uses_cirrus, env_template_literals) for an instance,
    localhost or remote. env_template_literals maps each KEY to the literal
    value pinned in env.template, excluding placeholder (KEY={{...}}) lines and
    comments; empty when not gitops / no env.template."""
    d = _helpers._SENTINEL
    qpath = _helpers._shell_quote(path)
    compose_cmd = _helpers._resolve_compose_cmd(inst)
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
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            template_literals[key.strip()] = val
    return running, uses_cirrus, template_literals


def _instance_consistency_lines(inst):
    """doctor lines for an instance's config<->runtime consistency, or [] when
    not applicable (no instance / unreadable state)."""
    if not inst:
        return []
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        return _k8s_config_consistency_lines(inst)
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
    running, uses_cirrus, template_literals = _gather_runtime(path, host)
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
#   - configData.crowdsec: acquis.yaml, whitelists.yaml
# Extensions/skins (PVC + boot-time symlinks, need a restart) and secrets are
# intentionally out of scope; gitops-managed instances are served by
# `canasta gitops status`/`diff` instead.

# Mirror of k8s_sync_config.yml's secret filter: these keys are stripped from
# .env before it lands in the web ConfigMap, so a change to them is not
# ConfigMap drift and must not be flagged.
_K8S_ENV_SECRET_KEYS = ("MYSQL_PASSWORD", "MW_SECRET_KEY")

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
    """Drop the secret-bearing lines the sync strips before embedding .env."""
    pat = re.compile(r"^(%s)=" % "|".join(_K8S_ENV_SECRET_KEYS))
    return [ln for ln in text.splitlines() if not pat.match(ln)]


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
    return channels


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
    podman_version = parts[22].strip() if len(parts) > 22 else "MISSING"
    if "Docker" in docker and "Docker Compose" in compose and daemon == "OK":
        pass
    elif podman_version != "MISSING":
        pass
    else:
        print("\nMissing core dependencies. Install Docker or Podman and ensure a runtime is running.",
              file=sys.stderr)
        return 1

    return 0
