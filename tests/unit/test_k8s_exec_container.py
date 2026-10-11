"""Every Kubernetes `kubectl exec` names its container with -c.

The web pod has init containers and the caddy pod can carry a CrowdSec
sidecar, so an exec without -c makes kubectl pick a container and print
"Defaulted container ..." into the CLI's output. Each pod is found by its
app.kubernetes.io/component label, and its main container has the same
name as that component in the chart.
"""

import io
import os
import subprocess
import sys
from argparse import Namespace

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402
from direct_commands import _helpers, info  # noqa: E402

TEMPLATES = os.path.join(REPO_ROOT, "roles", "orchestrator", "files",
                         "helm", "canasta", "templates")
EXEC_YML = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                        "exec.yml")


def _norm(text):
    return " ".join(text.split())


class TestChartContainerNames:
    """-c <component> is only right while each component's main container
    carries the component's name."""

    @pytest.mark.parametrize("template,name", [
        ("deployment-web.yaml", "web"),
        ("deployment-caddy.yaml", "caddy"),
        ("deployment-varnish.yaml", "varnish"),
        ("statefulset-db.yaml", "db"),
        ("statefulset-elasticsearch.yaml", "elasticsearch"),
    ])
    def test_main_container_is_named_for_its_component(self, template, name):
        with open(os.path.join(TEMPLATES, template)) as f:
            text = f.read()
        assert "app.kubernetes.io/component: %s" % name in text
        containers = text.split("containers:", 2)[-1]
        assert containers.lstrip().startswith("- name: %s\n" % name)

    def test_sidecar_container_is_named_for_its_component(self):
        with open(os.path.join(TEMPLATES, "sidecars.yaml")) as f:
            text = f.read()
        assert "app.kubernetes.io/component: {{ $sc.name | quote }}" in text
        assert "- name: {{ $sc.name | quote }}" in text


class TestHelpers:
    @pytest.mark.parametrize("service", ["web", "db", "cache"])
    def test_remote_exec_names_the_container(self, service):
        cmd = _helpers._k8s_remote_exec_cmd("mywiki", service, "true")
        assert '"$pod" -n canasta-mywiki -c %s -- ' % service in cmd

    def test_remote_exec_without_stdin_names_the_container(self):
        cmd = _helpers._k8s_remote_exec_cmd(
            "mywiki", "web", "true", forward_stdin=False)
        assert 'kubectl exec "$pod" -n canasta-mywiki -c web -- ' in cmd

    def test_local_exec_names_the_container(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(_helpers, "_k8s_get_pod",
                            lambda ns, svc: "pod-1")

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc, out = _helpers._exec_in_container(
            "mywiki", {"orchestrator": "kubernetes"}, "true", service="db")
        assert (rc, out) == (0, "ok")
        assert captured["argv"][:8] == [
            "kubectl", "exec", "pod-1", "-n", "canasta-mywiki",
            "-c", "db", "--"]

    def test_local_stream_names_the_container(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(_helpers, "_k8s_get_pod",
                            lambda ns, svc: "pod-1")

        def fake_popen(argv, **kw):
            captured["argv"] = argv
            return type("P", (), {"stdout": io.StringIO(""),
                                  "wait": lambda self: 0})()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        rc = _helpers._stream_in_container(
            "mywiki", {"orchestrator": "kubernetes"}, "true")
        assert rc == 0
        assert captured["argv"][:9] == [
            "kubectl", "exec", "-i", "pod-1", "-n", "canasta-mywiki",
            "-c", "web", "--"]


class TestInteractiveExec:
    def _capture(self, monkeypatch, host, service):
        captured = {}
        monkeypatch.setattr(canasta, "resolve_instance", lambda _id: {
            "id": "mywiki", "orchestrator": "k8s", "host": host,
            "path": "/tmp/i"})
        monkeypatch.setattr(
            canasta, "_redirect_stdin_from_file", lambda p: None)
        monkeypatch.setattr(
            canasta.os, "execvp",
            lambda f, argv: captured.update(file=f, argv=argv))
        monkeypatch.setattr(
            canasta.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, "pod-1", ""))
        canasta.handle_interactive_exec(Namespace(
            id="mywiki", service=service, exec_args=["php", "-v"],
            stdin_file=None))
        return captured

    def test_local_names_the_container(self, monkeypatch):
        argv = self._capture(monkeypatch, "localhost", "varnish")["argv"]
        assert argv[:8] == [
            "kubectl", "exec", "-it", "pod-1", "-n", "canasta-mywiki",
            "-c", "varnish"]

    def test_remote_names_the_container(self, monkeypatch):
        cap = self._capture(monkeypatch, "op@cp.example.com", "web")
        assert cap["file"] == "ssh"
        assert ('kubectl exec -it "$pod" -n canasta-mywiki -c web -- '
                in cap["argv"][-1])


class TestAnsibleExec:
    def test_k8s_exec_names_the_container(self):
        with open(EXEC_YML) as f:
            tasks = yaml.safe_load(f)
        block = next(t for t in tasks if t.get("name")
                     == "Resolve K8s pod and build exec command")["block"]
        build = next(t for t in block
                     if t.get("name") == "Build K8s exec command")
        cmd = _norm(build["ansible.builtin.set_fact"]["_exec_full_cmd"])
        assert ("-n {{ _k8s_namespace }} -c {{ exec_service | default('web') }} --"
                in cmd)


class TestVersionProbe:
    def test_probe_names_the_web_container(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(info._helpers, "_read_env_file",
                            lambda path, host: {"CANASTA_IMAGE": "img"})

        def fake_run(argv, **kw):
            captured["cmd"] = argv[-1]
            return subprocess.CompletedProcess(argv, 0, "3.1.0", "")

        monkeypatch.setattr(info.subprocess, "run", fake_run)
        info._read_instance_image("mywiki", {
            "orchestrator": "kubernetes", "host": "localhost",
            "path": "/tmp/i"})
        assert ' -n \'canasta-mywiki\' -c web -- ' in captured["cmd"]
