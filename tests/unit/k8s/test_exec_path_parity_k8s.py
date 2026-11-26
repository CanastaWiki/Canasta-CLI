"""The Python and Ansible container-exec paths agree on Kubernetes.

The direct path in canasta.py and exec.yml with k8s_get_pod.yml select a
pod the same way, use the same canasta-<id> namespace, default to the web
component, and pass -i for a piped stdin file. A remote Kubernetes instance
runs both the pod lookup and the exec on its registered host over ssh.
"""

import os
import types
from argparse import Namespace

import yaml

from test_exec_path_parity import EXEC_YML, ORCH_TASKS, _norm, _raw, canasta


def _command_cmds(task_file):
    """Return the normalized cmd string of every command/shell task in a
    flat Ansible task file."""
    with open(os.path.join(ORCH_TASKS, task_file)) as f:
        tasks = yaml.safe_load(f)
    out = []
    for task in tasks:
        for key in ("ansible.builtin.command", "ansible.builtin.shell"):
            val = task.get(key)
            if isinstance(val, dict) and isinstance(val.get("cmd"), str):
                out.append(_norm(val["cmd"]))
    return out


def _python_k8s_get_pods_cmd(monkeypatch, service):
    """Run the Python direct path far enough to capture the `kubectl get
    pods` argv it builds, with the exec itself stubbed out."""
    captured = {}
    monkeypatch.setattr(canasta, "resolve_instance", lambda _id: {
        "id": "rsdev", "orchestrator": "k8s",
        "host": "localhost", "path": "/tmp/i"})
    monkeypatch.setattr(canasta, "_redirect_stdin_from_file", lambda p: None)
    monkeypatch.setattr(canasta.os, "execvp", lambda f, argv: None)

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="pod-1")

    monkeypatch.setattr(canasta.subprocess, "run", fake_run)
    args = Namespace(id="rsdev", service=service,
                     exec_args=["php", "version"], stdin_file=None)
    canasta.handle_interactive_exec(args)
    return captured["cmd"]


K8S_GET_POD = _norm(_raw(os.path.join(ORCH_TASKS, "k8s_get_pod.yml")))


class TestK8sPodResolutionParity:
    """The Python kubectl-get-pods and k8s_get_pod.yml must select a pod the
    same way."""

    # Semantic fragments that must appear in both pod-resolution commands.
    SHARED = [
        "kubectl get pods",
        "app.kubernetes.io/component=",
        "--field-selector=status.phase=Running",
        ".metadata.name}",
    ]

    def test_python_and_ansible_share_pod_selectors(self, monkeypatch):
        py = _norm(" ".join(_python_k8s_get_pods_cmd(monkeypatch, "web")))
        get_pod_cmds = _command_cmds("k8s_get_pod.yml")
        assert get_pod_cmds, "no command task found in k8s_get_pod.yml"
        ansible = get_pod_cmds[0]
        for fragment in self.SHARED:
            assert fragment in py, "Python path missing %r" % fragment
            assert fragment in ansible, "Ansible path missing %r" % fragment

    def test_namespace_format_matches(self, monkeypatch):
        # Python builds "canasta-<id>"; exec.yml sets the same shape.
        py = _python_k8s_get_pods_cmd(monkeypatch, "web")
        assert "canasta-rsdev" in py
        assert '_k8s_namespace: "canasta-{{ instance_id }}"' in _raw(
            os.path.join(ORCH_TASKS, "exec.yml"))

    def test_service_defaults_to_web_on_both(self, monkeypatch):
        # Python: no service -> component=web.
        py = _norm(" ".join(_python_k8s_get_pods_cmd(monkeypatch, None)))
        assert "app.kubernetes.io/component=web" in py
        # Ansible: exec.yml passes exec_service|default('web') as the
        # component, and k8s_get_pod defaults it again.
        assert "default('web')" in K8S_GET_POD
        assert "exec_service | default('web')" in EXEC_YML


class TestStdinFlagParity:
    def test_k8s_stdin_uses_dash_i_on_both(self, monkeypatch):
        # Python k8s path with a stdin file uses -i (and drops -it).
        captured = {}
        monkeypatch.setattr(canasta, "resolve_instance", lambda _id: {
            "id": "x", "orchestrator": "k8s",
            "host": "localhost", "path": "/tmp/i"})
        monkeypatch.setattr(canasta, "_redirect_stdin_from_file", lambda p: None)
        monkeypatch.setattr(
            canasta.os, "execvp",
            lambda f, argv: captured.__setitem__("argv", list(argv)))
        monkeypatch.setattr(
            canasta.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="pod-1"))
        canasta.handle_interactive_exec(Namespace(
            id="x", service="web", exec_args=["php"], stdin_file="/tmp/p.txt"))
        assert "-i" in captured["argv"] and "-it" not in captured["argv"]
        # Ansible k8s build adds '-i ' when exec_stdin is set.
        assert "'-i '" in EXEC_YML and "exec_stdin" in EXEC_YML


class TestRemoteK8sExec:
    """A remote Kubernetes instance must run the pod lookup and the exec on
    its registered host via ssh — kubectl on the controller
    has no kubeconfig for the instance's cluster, so the previous local
    invocation always failed with 'no running pod found'."""

    def _capture(self, monkeypatch, **arg_overrides):
        captured = {}
        monkeypatch.setattr(canasta, "resolve_instance", lambda _id: {
            "id": "rsdev", "orchestrator": "k8s",
            "host": "op@cp.example.com", "path": "/tmp/i"})
        monkeypatch.setattr(
            canasta, "_redirect_stdin_from_file", lambda p: None)

        def fake_execvp(f, argv):
            captured["file"] = f
            captured["argv"] = argv

        monkeypatch.setattr(canasta.os, "execvp", fake_execvp)

        def no_local_kubectl(*a, **k):
            raise AssertionError(
                "local kubectl must not run for a remote instance")

        monkeypatch.setattr(canasta.subprocess, "run", no_local_kubectl)
        args = Namespace(id="rsdev", service="web",
                         exec_args=["php", "version"], stdin_file=None)
        for key, val in arg_overrides.items():
            setattr(args, key, val)
        canasta.handle_interactive_exec(args)
        return captured

    def test_remote_runs_via_ssh_not_local_kubectl(self, monkeypatch):
        cap = self._capture(monkeypatch)
        assert cap["file"] == "ssh"
        assert "op@cp.example.com" in cap["argv"]
        remote = cap["argv"][-1]
        assert "kubectl exec" in remote
        assert "canasta-rsdev" in remote

    def test_remote_pod_lookup_keeps_selector_parity(self, monkeypatch):
        # The ssh'd lookup must select a pod the same way as the local
        # path and k8s_get_pod.yml.
        remote = self._capture(monkeypatch)["argv"][-1]
        for fragment in TestK8sPodResolutionParity.SHARED:
            assert fragment in remote, "remote lookup missing %r" % fragment

    def test_remote_stdin_file_drops_the_tty(self, monkeypatch):
        cap = self._capture(monkeypatch, stdin_file="/tmp/payload.txt")
        assert "-T" in cap["argv"]
        remote = cap["argv"][-1]
        assert "kubectl exec -i " in remote
        assert "-it" not in remote

    def test_remote_interactive_allocates_a_tty(self, monkeypatch):
        cap = self._capture(monkeypatch)
        assert "-t" in cap["argv"]
        assert "kubectl exec -it " in cap["argv"][-1]

    def test_remote_missing_pod_fails_inside_the_ssh_command(self, monkeypatch):
        # The remote snippet, not the controller, reports the no-pod case.
        remote = self._capture(monkeypatch)["argv"][-1]
        assert "no running pod found for service 'web'" in remote
        assert "exit 1" in remote
