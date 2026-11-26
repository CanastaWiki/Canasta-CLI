"""canasta.py parsing and direct exec for Kubernetes instances.

create accepts the Kubernetes orchestrator and its Kubernetes-only flags,
--storage-class is restricted to Kubernetes, install accepts the k8s-cp
package, and the direct exec on a Kubernetes instance runs kubectl against
a Running pod, dropping the TTY for a piped stdin file.
"""

import types

import test_canasta_cli
from test_canasta_cli import canasta_cli

data = test_canasta_cli.data
parser = test_canasta_cli.parser


class TestBuildParser:
    def test_choice_flag(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "kubernetes"
        ])
        assert args.orchestrator == "kubernetes"


class TestCreateFlags:
    def test_skip_tls_accepted_with_kubernetes(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "kubernetes", "--skip-tls"
        ])
        assert args.skip_tls is True
        assert args.orchestrator == "kubernetes"

    def test_storage_class_accepted_with_kubernetes(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "kubernetes", "--storage-class", "nfs"
        ])
        assert args.storage_class == "nfs"

    def test_tls_email_accepted_with_kubernetes(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "kubernetes", "--tls-email", "test@example.com"
        ])
        assert args.tls_email == "test@example.com"


class TestOrchestratorValidation:
    def test_storage_class_rejected_with_compose(self, data):
        """--storage-class with --orchestrator compose should be rejected."""
        cmd_index = {c["name"]: c for c in data["commands"]}
        cmd_def = cmd_index["create"]
        # Simulate: orchestrator=compose, storage_class=nfs
        orchestrator = "compose"
        for param in cmd_def.get("parameters", []):
            orch_only = param.get("orchestrator_only")
            if not orch_only:
                continue
            if param["name"] == "storage_class":
                assert orch_only == "kubernetes"
                assert orchestrator != orch_only  # would be rejected


class TestInstallCommand:
    def test_install_multiple_packages(self, parser):
        # Was `k3s` — the internal name k8s-cp maps to, and the
        # user-facing name back when install shipped. It kept parsing
        # after the rename only because nothing constrained the argument.
        args = parser.parse_args(["install", "docker", "k8s-cp", "git-crypt"])
        assert args.command == "install"
        assert args.packages == ["docker", "k8s-cp", "git-crypt"]


class TestHandleInteractiveExecStdin:
    def _run(self, monkeypatch, stdin_file, orchestrator="compose",
             env=None, **inst_fields):
        from argparse import Namespace
        from direct_commands import _helpers
        calls = {}
        monkeypatch.setattr(
            _helpers, "_read_env_for",
            lambda _inst, key: (env or {}).get(key))
        inst = {
            "id": "rsdev", "orchestrator": orchestrator,
            "host": "localhost", "path": "/tmp/inst",
        }
        inst.update(inst_fields)
        monkeypatch.setattr(canasta_cli, "resolve_instance", lambda _id: inst)
        monkeypatch.setattr(canasta_cli.os, "chdir", lambda p: None)
        monkeypatch.setattr(
            canasta_cli, "_redirect_stdin_from_file",
            lambda p: calls.__setitem__("redirect", p))
        monkeypatch.setattr(
            canasta_cli.os, "execvp",
            lambda f, argv: calls.__setitem__("execvp", (f, list(argv))))
        if orchestrator in ("kubernetes", "k8s"):
            monkeypatch.setattr(
                canasta_cli.subprocess, "run",
                lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="pod-1"))
        args = Namespace(id="rsdev", service=None,
                         exec_args=["php", "edit"], stdin_file=stdin_file)
        canasta_cli.handle_interactive_exec(args)
        return calls

    def test_k8s_stdin_file_drops_tty(self, monkeypatch):
        calls = self._run(monkeypatch, "/tmp/page.txt", orchestrator="k8s")
        binary, argv = calls["execvp"]
        assert binary == "kubectl"
        assert "-i" in argv and "-it" not in argv
        assert calls["redirect"] == "/tmp/page.txt"

    def test_k8s_pod_resolution_filters_running(self, monkeypatch):
        """The pod lookup must select only Running pods, matching
        roles/orchestrator/tasks/k8s_get_pod.yml. Without the phase filter,
        items[0] during a rollout/scale/crash-loop can be a
        Pending/Terminating/Failed pod and the exec targets the wrong
        replica (or fails)."""
        from argparse import Namespace
        captured = {}

        monkeypatch.setattr(canasta_cli, "resolve_instance", lambda _id: {
            "id": "rsdev", "orchestrator": "k8s",
            "host": "localhost", "path": "/tmp/inst"})
        monkeypatch.setattr(
            canasta_cli, "_redirect_stdin_from_file", lambda p: None)
        monkeypatch.setattr(canasta_cli.os, "execvp", lambda f, argv: None)

        def fake_run(cmd, *a, **k):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stdout="pod-1")

        monkeypatch.setattr(canasta_cli.subprocess, "run", fake_run)
        args = Namespace(id="rsdev", service="web",
                         exec_args=["php", "version"], stdin_file=None)
        canasta_cli.handle_interactive_exec(args)
        assert "--field-selector=status.phase=Running" in captured["cmd"]
