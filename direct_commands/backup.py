"""backup list command."""

import json
import os
import subprocess
import sys


from . import _helpers
from ._helpers import register


# Pulling the restic image and listing a remote repository can take longer
# than an ordinary remote command, so local and remote runs share one limit.
_RESTIC_TIMEOUT = 60


def _run(host, cmd):
    """Run `cmd` on `host`; return (rc, stdout, stderr)."""
    if _helpers._is_localhost(host):
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=_RESTIC_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return 1, "", "Error: %s" % e
        return result.returncode, result.stdout, result.stderr
    # _ssh_run prints the remote stderr itself on failure.
    rc, stdout = _helpers._ssh_run(host, cmd, timeout=_RESTIC_TIMEOUT)
    return rc, stdout, ""


def env_warning(path, content):
    """Return the warning for quoted values or CRLF endings in .env, which
    reach restic literally through --env-file, or "" when there are none."""
    quoted_keys, has_crlf = _helpers._lint_env_content(content)
    problems = []
    if quoted_keys:
        problems.append("quoted values (%s)" % ", ".join(quoted_keys))
    if has_crlf:
        problems.append("CRLF line endings")
    if not problems:
        return ""
    return (
        "Warning: %s/.env has %s. Docker --env-file passes these to restic "
        "literally (unlike 'canasta config get', which strips them) — a "
        "common cause of restic 'wrong password' or backend auth failures. "
        "Remove surrounding quotes and use LF line endings."
        % (path, " and ".join(problems))
    )


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
    env_content = _helpers._read_env_content(path, host)
    env_vars = {k: v for k, v, c in _helpers._parse_env_entries(env_content)
                if not c and k}
    local_repo = env_vars.get("RESTIC_REPOSITORY", "")
    if not local_repo or not env_vars.get("RESTIC_PASSWORD", ""):
        print("RESTIC_REPOSITORY and RESTIC_PASSWORD must be set in .env.",
              file=sys.stderr)
        return 1
    warning = env_warning(path, env_content)
    if warning:
        print(warning, file=sys.stderr)
    local_mount = ""
    # Docker creates a missing bind-mount source; Podman refuses it.
    mkdir_repo = ""
    if local_repo.startswith("/"):
        qrepo = _helpers._shell_quote(local_repo)
        local_mount = "-v %s:%s" % (qrepo, qrepo)
        mkdir_repo = "mkdir -p -m 0755 %s || exit; " % qrepo

    qpath = _helpers._shell_quote(path)
    runtime = _helpers._resolve_inspect_cmd(inst)

    def restic(restic_args):
        return (
            "%(mkdir)s"
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
             "mkdir": mkdir_repo,
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
