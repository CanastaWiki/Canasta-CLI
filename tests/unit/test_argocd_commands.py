"""Tests for direct_commands/argocd.py — password / apps / ui commands.

These three operator-facing K8s commands had no coverage. Mock subprocess
(local path) and _ssh_run (remote path) so the tests exercise the command
logic without a live cluster.
"""

import os
import subprocess
import sys
from types import SimpleNamespace

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

from direct_commands import argocd, _helpers  # noqa: E402


def _run_result(returncode=0, stdout="", stderr=""):
    return type("R", (), {"returncode": returncode,
                          "stdout": stdout, "stderr": stderr})()


def _no_subprocess(*a, **k):
    raise AssertionError("remote path must not call subprocess directly")


def _capturing_call(store):
    """A subprocess.call stand-in that records the argv and returns 0."""
    def call(cmd):
        store["cmd"] = cmd
        return 0
    return call


class TestArgocdPassword:
    def test_local_prints_password(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: _run_result(0, "s3cret\n"))
        rc = argocd.cmd_argocd_password(SimpleNamespace(host=None))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "s3cret"

    def test_missing_secret_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: _run_result(0, ""))
        rc = argocd.cmd_argocd_password(SimpleNamespace(host=None))
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_nonzero_rc_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: _run_result(1, ""))
        assert argocd.cmd_argocd_password(SimpleNamespace(host=None)) == 1

    def test_remote_uses_ssh(self, monkeypatch, capsys):
        seen = {}

        def fake_ssh(host, cmd):
            seen["host"] = host
            return 0, "remotepw\n"

        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: False)
        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        monkeypatch.setattr(subprocess, "run", _no_subprocess)
        rc = argocd.cmd_argocd_password(SimpleNamespace(host="node1"))
        assert rc == 0
        assert seen["host"] == "node1"
        assert capsys.readouterr().out.strip() == "remotepw"


class TestArgocdApps:
    def test_local_success_prints(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: _run_result(0, "NAME  STATUS\n"))
        rc = argocd.cmd_argocd_apps(SimpleNamespace(host=None))
        assert rc == 0
        assert "NAME  STATUS" in capsys.readouterr().out

    def test_crd_missing_gives_install_hint(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _run_result(
                1, "", "error: the server doesn't have a resource type "
                       "\"applications\""))
        rc = argocd.cmd_argocd_apps(SimpleNamespace(host=None))
        assert rc == 1
        assert "doesn't appear to be installed" in capsys.readouterr().err

    def test_remote_uses_ssh(self, monkeypatch, capsys):
        seen = {}

        def fake_ssh(host, cmd):
            seen["cmd"] = cmd
            return 0, "NAME\n"

        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: False)
        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        monkeypatch.setattr(subprocess, "run", _no_subprocess)
        assert argocd.cmd_argocd_apps(SimpleNamespace(host="node1")) == 0
        assert "kubectl get applications" in seen["cmd"]


class TestArgocdUi:
    def test_local_port_forward_invocation(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: _run_result(0, "pw\n"))
        captured = {}
        monkeypatch.setattr(subprocess, "call", _capturing_call(captured))
        rc = argocd.cmd_argocd_ui(SimpleNamespace(host=None, port=None))
        assert rc == 0
        assert captured["cmd"][:2] == ["sh", "-c"]
        assert "port-forward svc/argocd-server" in captured["cmd"][2]
        assert "8443:443" in captured["cmd"][2]      # default port
        out = capsys.readouterr().out
        assert "admin password: pw" in out

    def test_custom_port(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: _run_result(0, "pw\n"))
        captured = {}
        monkeypatch.setattr(subprocess, "call", _capturing_call(captured))
        argocd.cmd_argocd_ui(SimpleNamespace(host=None, port=9000))
        assert "9000:443" in captured["cmd"][2]

    def test_remote_builds_ssh_tunnel(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_is_localhost", lambda h: False)
        monkeypatch.setattr(_helpers, "_ssh_run",
                            lambda host, cmd: (0, "pw\n"))
        monkeypatch.setattr(_helpers, "_resolve_ssh_target",
                            lambda h: "admin@node1")
        monkeypatch.setattr(_helpers, "_ssh_args", lambda: [])
        captured = {}
        monkeypatch.setattr(subprocess, "call", _capturing_call(captured))
        rc = argocd.cmd_argocd_ui(SimpleNamespace(host="node1", port=None))
        assert rc == 0
        cmd = captured["cmd"]
        assert cmd[0] == "ssh"
        assert "-L" in cmd
        assert "8443:localhost:8443" in cmd
        assert "admin@node1" in cmd
        assert any("port-forward svc/argocd-server" in a for a in cmd)
