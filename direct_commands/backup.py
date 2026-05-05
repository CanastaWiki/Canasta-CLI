"""backup list command."""

import os
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


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
    cmd = (
        "docker volume create %(vol)s >/dev/null 2>&1; "
        "docker run --rm -i "
        "--env-file %(path)s/.env "
        "-v %(vol)s:/currentsnapshot "
        "%(local_mount)s "
        "restic/restic "
        "--cache-dir /tmp/restic-cache "
        "snapshots"
    ) % {"vol": _helpers._shell_quote(bvol), "path": qpath, "local_mount": local_mount}

    if _helpers._is_localhost(host):
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=60,
            )
            if result.stdout.strip():
                print(result.stdout.strip())
            return result.returncode
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1

    rc, stdout = _helpers._ssh_run(host, cmd)
    if stdout.strip():
        print(stdout.strip())
    return rc
