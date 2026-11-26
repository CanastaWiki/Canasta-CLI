"""restic arguments reach the Kubernetes backup pod's command intact.

The restic container command built for the backup Job keeps each argument
whole, including ones that contain spaces or shell metacharacters.
"""

import os
import shlex

from test_backup_args_quoting import ARGS, ORCH, _render, _task


def test_k8s_restic_args_stay_whole():
    task = _task(os.path.join(ORCH, "k8s_run_backup.yml"),
                 "Build the restic container command")
    cmd = _render(task["ansible.builtin.set_fact"]["_restic_command"],
                  backup_args=ARGS + ["--keep-last", 3])
    assert shlex.split(cmd) == [
        "restic", "--cache-dir", "/tmp/restic-cache"] + ARGS + [
        "--keep-last", "3"]
