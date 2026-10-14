"""gitops sync on a stopped Kubernetes instance reports that it is stopped
and leaves it stopped, and a sync that never completes says so.

The fake kubectl logs every call, prints FAKE_OUT (standing in for the web
deployment's spec.replicas) and exits FAKE_RC.
"""

import os
import stat
import subprocess
import sys

import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
TRIGGER = os.path.join(TASKS, "gitops_trigger_sync.yml")
ARGO_SYNC = os.path.join(TASKS, "k8s_argocd_sync.yml")

FAKE_KUBECTL = """#!/bin/sh
echo "kubectl $*" >> "$CMD_LOG"
printf '%s' "$FAKE_OUT"
exit "${FAKE_RC:-0}"
"""

STOPPED_MSG = "Instance 'site' is stopped, so it was left stopped"
REFRESH = "Trigger Argo CD sync via refresh annotation"


def _task_result(out, name):
    """The first line Ansible printed for task `name`, e.g. 'skipping: ...'."""
    lines = out.splitlines()
    i = next(n for n, line in enumerate(lines)
             if line.startswith("TASK [%s]" % name))
    return next(line for line in lines[i + 1:] if line.strip())


def _run(tmp_path, tasks, play_vars, fake_out="", fake_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / "kubectl"
    exe.write_text(FAKE_KUBECTL)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "cmd.log"
    log.write_text("")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH="%s:%s" % (bindir, os.environ.get("PATH", "")),
               FAKE_OUT=fake_out, FAKE_RC=str(fake_rc), CMD_LOG=str(log),
               ANSIBLE_STDOUT_CALLBACK="default",
               ANSIBLE_DISPLAY_SKIPPED_HOSTS="true",
               KUBECONFIG=str(tmp_path / "no-such-kubeconfig"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, proc.stdout + proc.stderr, log.read_text()


class TestSyncStopped:
    TASKS = [{"ansible.builtin.include_tasks": TRIGGER}]
    VARS = {"instance_id": "site", "instance_orchestrator": "kubernetes"}

    def test_stopped_instance_reports_and_succeeds(self, tmp_path):
        proc, out, log = _run(tmp_path, self.TASKS, self.VARS, "0")
        assert proc.returncode == 0, out
        assert STOPPED_MSG in out
        assert "'canasta start -i site'" in out
        # Only the replica check ran: nothing was scaled or patched.
        assert log.splitlines() == [
            "kubectl get deployment canasta-site-web -n canasta-site "
            "-o jsonpath={.spec.replicas}"]
        assert _task_result(out, REFRESH).startswith("skipping")

    def test_running_instance_attempts_the_sync(self, tmp_path):
        _, out, _ = _run(tmp_path, self.TASKS, self.VARS, "1")
        assert STOPPED_MSG not in out
        assert not _task_result(out, REFRESH).startswith("skipping")

    def test_missing_deployment_is_not_treated_as_stopped(self, tmp_path):
        _, out, _ = _run(tmp_path, self.TASKS, self.VARS, "", fake_rc=1)
        assert STOPPED_MSG not in out
        assert not _task_result(out, REFRESH).startswith("skipping")


def _report_tasks():
    """The tasks after the wait, which report a sync that did not complete."""
    with open(ARGO_SYNC) as f:
        tasks = yaml.safe_load(f)
    block = next(t for t in tasks if t.get("name") == "Sync the running instance")
    names = [t.get("name") for t in block["block"]]
    return block["block"][names.index("Wait for Argo CD sync completion") + 1:]


class TestSyncTimeout:
    def _status(self, result):
        return [
            {"ansible.builtin.set_fact": {"_argocd_sync_status": result}},
            *_report_tasks(),
            {"ansible.builtin.debug": {"msg": "CONTINUED"}},
        ]

    def test_wait_exhausted_reports_last_state(self, tmp_path):
        result = {"failed": True, "attempts": 60, "resources": [
            {"status": {"sync": {"status": "OutOfSync"},
                        "health": {"status": "Progressing"}}}]}
        proc, out, _ = _run(tmp_path, self._status(result),
                            {"instance_id": "site"})
        assert proc.returncode != 0
        out = " ".join(out.split())
        assert "as Synced and Healthy within 5 minutes" in out
        assert "sync: OutOfSync, health: Progressing" in out

    def test_missing_application_reports_unknown(self, tmp_path):
        result = {"failed": True, "attempts": 60, "resources": []}
        proc, out, _ = _run(tmp_path, self._status(result),
                            {"instance_id": "site"})
        assert proc.returncode != 0
        assert "sync: Unknown, health: Unknown" in " ".join(out.split())

    def test_completed_sync_continues(self, tmp_path):
        result = {"failed": False, "resources": [
            {"status": {"sync": {"status": "Synced"},
                        "health": {"status": "Healthy"}}}]}
        proc, out, _ = _run(tmp_path, self._status(result),
                            {"instance_id": "site"})
        assert proc.returncode == 0, out
        assert "CONTINUED" in out
        assert "Argo CD sync completed for 'canasta-site'" in out
