"""A failed Helm deploy during start does not leave a stopped instance down.

restart on Kubernetes stops first (every workload scaled to zero, Argo CD
auto-sync suspended) and then deploys. When that deploy failed, nothing
scaled the workloads back up or resumed auto-sync, so a running instance
ended up down.
"""

import os
import stat
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
DEPLOY = os.path.join(TASKS, "k8s_start_helm_deploy.yml")
START = os.path.join(TASKS, "start.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

OWNERSHIP_ERROR = (
    "Error: UPGRADE FAILED: Unable to continue with update: ConfigMap "
    "\"canasta-demo-crowdsec-config\" in namespace \"canasta-demo\" exists "
    "and cannot be imported into the current release: invalid ownership "
    "metadata")


def _fake(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


FAKE_K8S_INFO = """#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule
m = AnsibleModule(argument_spec=dict(kind=dict(), namespace=dict()))
m.exit_json(changed=False, resources=[])
"""


def _run(tmp_path, replicas_before, helm_rc):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    _fake(bindir, "helm",
          'echo "helm $*" >> %s\n' % log
          + ('echo \'%s\' >&2\nexit 1\n' % OWNERSHIP_ERROR
             if helm_rc else "exit 0\n"))
    _fake(bindir, "kubectl",
          'echo "kubectl $*" >> %s\n' % log
          + 'case "$*" in\n'
          '  "get application"*) echo canasta-demo ;;\n'
          '  "get statefulset"*) echo statefulset.apps/canasta-demo-db ;;\n'
          'esac\n'
          'exit 0\n')
    # helm_deploy.yml names kubernetes.core.k8s_info; Ansible resolves it
    # even when the task is skipped, and CI has no kubernetes.core.
    mods = tmp_path / "ac" / "ansible_collections" / "kubernetes" / "core" \
        / "plugins" / "modules"
    mods.mkdir(parents=True)
    (mods / "k8s_info.py").write_text(FAKE_K8S_INFO)
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "values.yaml").write_text("instance:\n  id: demo\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "canasta_root": REPO_ROOT,
            "instance_id": "demo",
            "instance_path": str(inst),
            "_k8s_namespace": "canasta-demo",
            "_start_values": {"web": {"replicaCount": 2}},
            "_current_replicas": {"stdout": replicas_before},
            "rx_delay": 1,
            "ansible_python_interpreter": sys.executable,
        },
        "tasks": [{"ansible.builtin.include_tasks": DEPLOY}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "ac"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def _scaled_up(calls):
    return any(c.startswith("kubectl scale deployment --all --replicas=1")
               for c in calls)


def _sync_resumed(calls):
    return any(c.startswith("kubectl patch application canasta-demo")
               and '"automated"' in c for c in calls)


@pytest.fixture(autouse=True)
def _posix_only():
    if os.name != "posix":
        pytest.skip("needs a POSIX shell for the fake binaries")


class TestFailedDeploy:
    def test_a_stopped_instance_is_brought_back_up(self, tmp_path):
        proc, calls = _run(tmp_path, replicas_before="0", helm_rc=1)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "invalid ownership metadata" in out
        assert _scaled_up(calls), calls
        assert any(c.startswith("kubectl scale statefulset --all --replicas=1")
                   for c in calls), calls
        assert any("canasta-demo-web --replicas=2" in c for c in calls), calls
        assert _sync_resumed(calls), calls
        # The deploy ran before anything was scaled back up.
        first_helm = next(i for i, c in enumerate(calls) if c.startswith("helm"))
        first_scale = next(i for i, c in enumerate(calls)
                           if c.startswith("kubectl scale"))
        assert first_helm < first_scale

    def test_a_running_instance_is_not_rescaled(self, tmp_path):
        proc, calls = _run(tmp_path, replicas_before="2", helm_rc=1)
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert not any(c.startswith("kubectl scale") for c in calls), calls
        assert _sync_resumed(calls), calls


class TestSuccessfulDeploy:
    def test_a_stopped_instance_is_scaled_up(self, tmp_path):
        proc, calls = _run(tmp_path, replicas_before="0", helm_rc=0)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _scaled_up(calls), calls
        # Auto-sync resumes at the end of start, after the readiness waits.
        assert not _sync_resumed(calls), calls

    def test_a_running_instance_is_left_alone(self, tmp_path):
        proc, calls = _run(tmp_path, replicas_before="1", helm_rc=0)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not any(c.startswith("kubectl scale") for c in calls), calls


class TestWiring:
    def test_start_deploys_through_the_restoring_wrapper(self):
        with open(START) as f:
            text = f.read()
        assert "include_tasks: k8s_start_helm_deploy.yml" in text
        assert "include_tasks: helm_deploy.yml" not in text
