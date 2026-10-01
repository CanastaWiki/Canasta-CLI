"""backup list command."""

import json
import os
import subprocess
import sys


from . import _helpers
from ._helpers import register


def _run(host, cmd):
    """Run `cmd` on `host`; return (rc, stdout, stderr)."""
    if _helpers._is_localhost(host):
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return 1, "", "Error: %s" % e
        return result.returncode, result.stdout, result.stderr
    # _ssh_run prints the remote stderr itself on failure.
    rc, stdout = _helpers._ssh_run(host, cmd)
    return rc, stdout, ""


def incomplete_warning(snapshots_json):
    """Return the warning for `restic snapshots --json --tag INCOMPLETE`
    output, or "" when there is nothing to warn about."""
    try:
        snapshots = json.loads(snapshots_json or "[]")
    except ValueError:
        return ""
    if not snapshots:
        return ""
    lines = [
        "WARNING: %d INCOMPLETE snapshot(s), missing databases that failed "
        "to dump:" % len(snapshots)
    ]
    for snap in sorted(snapshots, key=lambda s: s.get("time", "")):
        missing = [t[len("missing-db:"):] for t in snap.get("tags") or []
                   if t.startswith("missing-db:")]
        lines.append("  %s  %s  missing: %s" % (
            snap.get("short_id", snap.get("id", "")[:8]),
            snap.get("time", "")[:19].replace("T", " "),
            ", ".join(missing) or "(unrecorded)",
        ))
    return "\n".join(lines)


@register("backup_list")
def cmd_backup_list(args):
    inst_id, inst = _helpers._resolve_instance(args)
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    orchestrator = inst.get("orchestrator", "compose")

    if orchestrator in ("kubernetes", "k8s"):
        return _helpers.FALLBACK

    bvol = "canasta-backup-%s" % os.path.basename(path)
    env_vars = _helpers._read_env_file(path, host)
    local_repo = env_vars.get("RESTIC_REPOSITORY", "")
    local_mount = ""
    if local_repo.startswith("/"):
        qrepo = _helpers._shell_quote(local_repo)
        local_mount = "-v %s:%s" % (qrepo, qrepo)

    qpath = _helpers._shell_quote(path)
    runtime = _helpers._resolve_inspect_cmd(inst)

    def restic(restic_args):
        return (
            "%(rt)s volume create %(vol)s >/dev/null 2>&1; "
            "%(rt)s run --rm -i "
            "--env-file %(path)s/.env "
            "-v %(vol)s:/currentsnapshot "
            "%(local_mount)s "
            "docker.io/restic/restic "
            "--cache-dir /tmp/restic-cache "
            "%(args)s"
        ) % {"vol": _helpers._shell_quote(bvol), "path": qpath,
             "local_mount": local_mount, "rt": runtime,
             "args": restic_args}

    rc, stdout, stderr = _run(host, restic("snapshots"))
    if stdout.strip():
        print(stdout.strip())
    # restic writes its diagnostics to stderr, so dropping them
    # leaves a failure (unreachable repository, wrong password,
    # locked repo) indistinguishable from a repository with no
    # snapshots.
    if rc != 0:
        if stderr.strip():
            print(stderr.strip(), file=sys.stderr)
        return rc

    # The table shows INCOMPLETE as just another tag, so spell out what
    # each INCOMPLETE snapshot is missing below it.
    if "INCOMPLETE" in stdout:
        jrc, jout, _ = _run(
            host, restic("snapshots --json --tag INCOMPLETE"))
        warning = incomplete_warning(jout) if jrc == 0 else (
            "WARNING: snapshots tagged INCOMPLETE are missing databases "
            "that failed to dump.")
        if warning:
            print()
            print(warning)
    return rc
