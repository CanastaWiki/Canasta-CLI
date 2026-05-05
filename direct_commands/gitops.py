"""gitops status, gitops diff commands."""

import json
import os
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


def _gitops_status_script(path):
    """Build a batched shell script that gathers all gitops status info."""
    d = _helpers._SENTINEL
    qp = _helpers._shell_quote(path)
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
    parts = stdout.split(_helpers._SENTINEL + "\n")

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
    parts = stdout.split(_helpers._SENTINEL + "\n")
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
    inst_id, inst = _helpers._resolve_instance(args)
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    orchestrator = inst.get("orchestrator", "compose")

    script = _gitops_status_script(path)

    if _helpers._is_localhost(host):
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
        rc, stdout = _helpers._ssh_run(host, script)
        if rc != 0 and not stdout.strip():
            print("Error: failed to connect to %s" % host, file=sys.stderr)
            return 1

    if orchestrator in ("kubernetes", "k8s"):
        argocd = _gitops_argocd_status(inst_id)
        print(_parse_gitops_status_k8s(stdout, inst_id, argocd))
    else:
        print(_parse_gitops_status(stdout, inst_id))
    return 0

def _gitops_diff_script(path):
    d = _helpers._SENTINEL
    qp = _helpers._shell_quote(path)
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
    parts = stdout.split(_helpers._SENTINEL + "\n")

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
    inst_id, inst = _helpers._resolve_instance(args)
    orchestrator = inst.get("orchestrator", "compose")

    if orchestrator in ("kubernetes", "k8s"):
        return _helpers.FALLBACK

    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    script = _gitops_diff_script(path)

    if _helpers._is_localhost(host):
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
        rc, stdout = _helpers._ssh_run(host, script)
        if rc != 0 and not stdout.strip():
            print("Error: failed to connect to %s" % host, file=sys.stderr)
            return 1

    print(_parse_gitops_diff(stdout))
    return 0
