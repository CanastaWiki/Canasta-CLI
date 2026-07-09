"""gitops status, gitops diff commands."""

import json
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


def _git_ssh_env_prefix(ssh_key):
    """Shell `export GIT_SSH_COMMAND=...; ` prefix for a given SSH key.

    Mirrors the Ansible gitops_git_env convention (accept-new host keys,
    explicit known_hosts). Returns an empty string when no key is given,
    leaving git's ambient SSH config (agent forwarding / deploy key)
    untouched.
    """
    if not ssh_key:
        return ""
    return (
        'export GIT_SSH_COMMAND="ssh -i %s '
        '-o StrictHostKeyChecking=accept-new '
        '-o UserKnownHostsFile=~/.ssh/known_hosts"; '
        % _helpers._shell_quote(ssh_key)
    )


def _gitops_status_script(path, ssh_key=None):
    """Build a batched shell script that gathers all gitops status info."""
    d = _helpers._SENTINEL
    qp = _helpers._shell_quote(path)
    return (
        "cd %(p)s; "
        "%(ssh)s"
        "cat .gitops-host 2>/dev/null || echo MISSING; "
        "echo '%(d)s'; "
        "cat hosts/hosts.yaml 2>/dev/null || echo MISSING; "
        "echo '%(d)s'; "
        "git rev-parse --short HEAD 2>/dev/null || echo none; "
        "echo '%(d)s'; "
        "cat .gitops-applied 2>/dev/null || echo none; "
        "echo '%(d)s'; "
        "git diff --cached --name-status 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff --name-status 2>/dev/null; "
        "echo '%(d)s'; "
        "git fetch 2>/dev/null; "
        "git rev-list --left-right --count HEAD...@{upstream} 2>/dev/null || echo '0\t0'; "
        "echo '%(d)s'; "
        "cat config/wikis.yaml 2>/dev/null; "
        "echo '%(d)s'; "
        "cat wikis.yaml.template 2>/dev/null; "
        "echo '%(d)s'; "
        # Untracked files, parsed from porcelain '?? ' lines below. Uses
        # `git status` (not `git ls-files --directory`, which over-reports
        # dirs whose contents are all ignored and mishandles submodules) so
        # it matches what `git status` shows exactly.
        "git status --porcelain 2>/dev/null"
    ) % {"p": qp, "d": d, "ssh": _git_ssh_env_prefix(ssh_key)}


_URL_LINE_RE = re.compile(r"^[ \t]*url:.*$", re.MULTILINE)

# git diff --name-status codes → human labels (git's own vocabulary), so a
# staged/unstaged deletion doesn't read as content being pushed.
_CHANGE_LABELS = {
    "A": "new file", "M": "modified", "D": "deleted",
    "R": "renamed", "C": "copied", "T": "typechange",
}


def _parse_name_status(raw):
    """Parse `git diff --name-status` output into (label, path) pairs.

    Each line is `<CODE>\\t<path>` (rename/copy is `<CODE>\\t<old>\\t<path>` —
    take the last field for the current path). Unknown codes fall back to the
    raw code so nothing is silently dropped."""
    changes = []
    for ln in raw.split("\n"):
        if not ln.strip():
            continue
        fields = ln.split("\t")
        code = fields[0][:1]
        path = fields[-1]
        changes.append((_CHANGE_LABELS.get(code, fields[0]), path))
    return changes


def _wikis_uncaptured_edit(live_raw, tmpl_raw):
    """True if config/wikis.yaml has literal edits not yet in the template.

    config/wikis.yaml is rendered from wikis.yaml.template, so a direct
    edit to a literal field (e.g. a wiki's display name) is gitignored and
    invisible to git status until 'gitops add' reconciles it. Compare the
    two per wiki id, ignoring the host-specific url (a {{placeholder}} in
    the template), so the advisory fires only on a genuine uncaptured edit.
    Returns False when either file is absent or unparseable (e.g. a
    non-gitops instance, or one whose wikis.yaml.template has not been
    created/backfilled yet). Applies to both Compose and K8s gitops.
    """
    if not (live_raw or "").strip() or not (tmpl_raw or "").strip():
        return False
    # The template's `url:` lines hold {{placeholder}} values that are not
    # valid YAML, so strip url lines from both before parsing. url is
    # host-specific and excluded from the comparison anyway.
    live_clean = _URL_LINE_RE.sub("", live_raw)
    tmpl_clean = _URL_LINE_RE.sub("", tmpl_raw)
    try:
        live = yaml.safe_load(live_clean) or {}
        tmpl = yaml.safe_load(tmpl_clean) or {}
    except yaml.YAMLError:
        return False

    def _by_id(doc):
        out = {}
        for w in (doc.get("wikis") or []) if isinstance(doc, dict) else []:
            if isinstance(w, dict) and "id" in w:
                out[w["id"]] = {k: v for k, v in w.items() if k != "url"}
        return out

    return _by_id(live) != _by_id(tmpl)


def _parse_working_tree_changes(parts):
    """Extract (staged, unstaged, untracked, wikis_drift) from the shared
    status-probe output (see _gitops_status_script). Used by both the Compose
    and K8s formatters so their working-tree advisories can't diverge."""
    staged_raw = parts[4].strip() if len(parts) > 4 else ""
    unstaged_raw = parts[5].strip() if len(parts) > 5 else ""
    # (label, path) pairs from git diff --name-status, so deletions render
    # distinctly from adds/modifications.
    staged = _parse_name_status(staged_raw)
    unstaged = _parse_name_status(unstaged_raw)

    live_wikis_raw = parts[7] if len(parts) > 7 else ""
    tmpl_wikis_raw = parts[8] if len(parts) > 8 else ""
    wikis_drift = _wikis_uncaptured_edit(live_wikis_raw, tmpl_wikis_raw)

    # parts[9] is `git status --porcelain`; untracked entries are the
    # '?? <path>' lines (staged/modified come from the git diff sections).
    status_raw = parts[9].strip() if len(parts) > 9 else ""
    untracked = [ln[3:] for ln in status_raw.split("\n") if ln.startswith("?? ")]
    return staged, unstaged, untracked, wikis_drift


def _working_tree_advisory_lines(staged, unstaged, untracked, wikis_drift):
    """Advisory lines for uncommitted / untracked / uncaptured working-tree
    changes. Empty list when the tree is clean."""
    lines = []
    if staged:
        lines.append("Staged for push (%d files):" % len(staged))
        lines.extend("  %s: %s" % (label, path) for label, path in staged)
        lines.append("")
    if unstaged:
        lines.append("Unstaged changes (%d files):" % len(unstaged))
        lines.extend("  %s: %s" % (label, path) for label, path in unstaged)
        lines.append("")
    if untracked:
        lines.append("Untracked files (%d):" % len(untracked))
        lines.extend("  %s" % f for f in untracked)
        # wikis.yaml.template is captured by reconciling the live
        # config/wikis.yaml, not a plain 'gitops add' of the template — the
        # latter skips the reconcile and can stage a stale template, dropping
        # an uncaptured config/wikis.yaml edit. Point at the right command.
        if "wikis.yaml.template" in untracked:
            lines.append(
                "  for wikis.yaml.template, run "
                "'canasta gitops add config/wikis.yaml'."
            )
        if any(f != "wikis.yaml.template" for f in untracked):
            lines.append(
                "  capture with 'canasta gitops add <file>' "
                "(or add to .gitignore)."
            )
        lines.append("")
    if wikis_drift:
        lines.append("Uncaptured config/wikis.yaml edits (e.g. wiki display name):")
        lines.append(
            "  run 'canasta gitops add config/wikis.yaml' to capture and "
            "stage them."
        )
        lines.append("")
    return lines


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
    revcount_raw = parts[6].strip() if len(parts) > 6 else "0\t0"

    staged, unstaged, untracked, wikis_drift = _parse_working_tree_changes(parts)

    try:
        revcount_parts = revcount_raw.split("\t")
        ahead = int(revcount_parts[0])
        behind = int(revcount_parts[1]) if len(revcount_parts) > 1 else 0
    except (ValueError, IndexError):
        ahead, behind = 0, 0

    # No .gitops-host marker and no git repository means the instance is simply
    # not under GitOps management. Reporting "No changes / Up to date with
    # remote" here would falsely imply a healthy, in-sync managed instance —
    # there is no remote to be in sync with.
    if hostname == "unknown" and commit == "none":
        return "\n".join([
            "Canasta ID:     %s" % instance_id,
            "GitOps:         not configured for this instance.",
            "",
            "This instance is not under GitOps management (no .gitops-host "
            "marker and no git repository).",
            "Set it up with 'canasta gitops init' (new repo) or "
            "'canasta gitops join' (existing repo).",
        ])

    lines = [
        "Host:           %s" % hostname,
        "Role:           %s" % role,
        "Canasta ID:     %s" % instance_id,
        "Pull requests:  %s" % str(pull_requests),
        "Current commit: %s" % commit,
        "Last applied:   %s" % (applied if applied != "none" else ""),
        "",
    ]

    changes = _working_tree_advisory_lines(staged, unstaged, untracked, wikis_drift)
    if changes:
        lines.extend(changes)
    else:
        lines.append("No changes.")
        lines.append("")

    if ahead > 0:
        lines.append("Ahead of remote by %d commit(s)." % ahead)
    elif behind > 0:
        lines.append("Behind remote by %d commit(s)." % behind)
    else:
        lines.append("Up to date with remote.")

    return "\n".join(lines)


def _gitops_argocd_status(instance_id, host="localhost"):
    """Query Argo CD Application for this instance; return parsed status.

    Returns a tuple (sync_status, health, last_sync, revision). Falls
    back to 'Not registered' / 'N/A' sentinels when Argo CD isn't
    installed or no Application exists — matches the Ansible path's
    behavior for K8s instances without Argo CD.

    For remote hosts, SSH and run kubectl on the host — its kubeconfig
    points at the cluster it's part of. Running kubectl on the laptop
    would query whatever the laptop's kubeconfig points at, reporting
    'Not registered' for instances that are actually Synced.
    """
    if _helpers._is_localhost(host):
        try:
            result = subprocess.run(
                ["kubectl", "get", "application", "canasta-%s" % instance_id,
                 "-n", "argocd", "-o", "json"],
                capture_output=True, text=True, timeout=10,
            )
            rc, stdout = result.returncode, result.stdout
        except (subprocess.TimeoutExpired, OSError):
            return ("Not registered", "N/A", "never", "unknown")
    else:
        rc, stdout = _helpers._ssh_run(
            host,
            "kubectl get application canasta-%s -n argocd -o json"
            % instance_id,
        )
    if rc != 0:
        return ("Not registered", "N/A", "never", "unknown")
    try:
        app = json.loads(stdout)
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

    # Uncommitted/untracked working-tree changes and uncaptured config/wikis.yaml
    # edits apply to K8s gitops too (e.g. an upgrade backfilling
    # wikis.yaml.template), so surface the same advisories as the Compose path.
    staged, unstaged, untracked, wikis_drift = _parse_working_tree_changes(parts)

    lines = [
        "Host:             %s" % hostname,
        "Canasta ID:       %s" % instance_id,
        "Current commit:   %s" % commit,
        "Ahead of remote:  %d" % ahead,
        "Behind remote:    %d" % behind,
        "",
    ]
    lines.extend(
        _working_tree_advisory_lines(staged, unstaged, untracked, wikis_drift)
    )
    lines.extend([
        "Argo CD:",
        "  Sync status:    %s" % sync_status,
        "  Health status:  %s" % health,
        "  Last sync:      %s" % last_sync,
        "  Applied rev:    %s" % revision,
    ])
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

    script = _gitops_status_script(path, ssh_key=getattr(args, "ssh_key", None))

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
        argocd = _gitops_argocd_status(inst_id, host)
        print(_parse_gitops_status_k8s(stdout, inst_id, argocd))
    else:
        print(_parse_gitops_status(stdout, inst_id))
    return 0

def _gitops_diff_script(path, stat=False):
    # For each of the three boundaries we emit two sections: a
    # --name-only list (for the file count and the restart/update
    # heuristics) followed by the actual patch the user asked to see.
    # Uncommitted uses `HEAD` so staged *and* unstaged changes both
    # show up (plain `git diff` would miss staged files). Local and
    # remote use three-dot ranges so each side shows what its own
    # commits introduced since the branches diverged, rather than the
    # symmetric two-dot file set.
    d = _helpers._SENTINEL
    qp = _helpers._shell_quote(path)
    fmt = "--stat" if stat else "-p"
    return (
        "cd %(p)s; "
        "git diff --name-only HEAD 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff %(f)s HEAD 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff --name-only @{upstream}...HEAD 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff %(f)s @{upstream}...HEAD 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff --name-only HEAD...@{upstream} 2>/dev/null; "
        "echo '%(d)s'; "
        "git diff %(f)s HEAD...@{upstream} 2>/dev/null; "
        "echo '%(d)s'; "
        "git submodule status 2>/dev/null"
    ) % {"p": qp, "d": d, "f": fmt}


def _parse_gitops_diff(stdout):
    parts = stdout.split(_helpers._SENTINEL + "\n")

    def _names(i):
        raw = parts[i].strip() if len(parts) > i else ""
        return sorted(raw.split("\n")) if raw else []

    def _patch(i):
        return parts[i].strip("\n") if len(parts) > i else ""

    uc_files, uc_patch = _names(0), _patch(1)
    lc_files, lc_patch = _names(2), _patch(3)
    rc_files, rc_patch = _names(4), _patch(5)
    submodules_raw = parts[6].strip() if len(parts) > 6 else ""

    lines = []

    def _section(title, files, patch):
        lines.append("%s: %d file(s)" % (title, len(files)))
        if patch:
            lines.append(patch)
        lines.append("")

    _section("Uncommitted changes", uc_files, uc_patch)
    _section("Local changes (not yet pushed)", lc_files, lc_patch)
    _section("Remote changes (would be applied on pull)", rc_files, rc_patch)

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
    script = _gitops_diff_script(path, stat=bool(getattr(args, "stat", False)))

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
