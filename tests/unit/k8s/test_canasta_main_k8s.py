"""canasta.py main() for Kubernetes instances: the k8s orchestrator alias,
refusal of commands declared for one orchestrator on an instance of the
other, and the Kubernetes deprecation warning."""

import pytest

import test_canasta_main
from test_canasta_main import _run, canasta

harness = test_canasta_main.harness


class TestOrchestratorNormalization:
    def test_k8s_alias_becomes_kubernetes(self, harness, monkeypatch):
        assert _run(monkeypatch, [
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "k8s",
            "--skip-argocd-install",
        ]) == "exec"
        assert harness.ansible_calls[0][1].orchestrator == "kubernetes"


class TestCommandOrchestrators:
    @staticmethod
    def _register(tmp_path, orchestrator):
        import json
        (tmp_path / "conf.json").write_text(json.dumps({"Instances": {
            "mysite": {"path": str(tmp_path / "mysite"), "host": "localhost",
                       "orchestrator": orchestrator},
        }}))

    def test_direct_command_refused_on_compose(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "compose")
        assert _run(monkeypatch, ["scale", "-i", "mysite", "-r", "2"]) == 1
        err = capsys.readouterr().err
        assert ("'canasta scale' is only supported on Kubernetes instances"
                in err)
        assert "PHP-FPM" in err
        assert harness.direct_calls == []

    def test_direct_command_runs_on_k8s_alias(
            self, harness, monkeypatch, tmp_path):
        self._register(tmp_path, "k8s")
        assert _run(monkeypatch, ["scale", "-i", "mysite", "-r", "2"]) == 0
        assert [c[0] for c in harness.direct_calls] == ["scale"]

    @pytest.mark.parametrize("argv,label", [
        (["extension", "add", "X", "-i", "mysite"], "extension add"),
        (["skin", "remove", "X", "-i", "mysite"], "skin remove"),
        (["devmode", "enable", "-i", "mysite"], "devmode enable"),
        (["devmode", "disable", "-i", "mysite"], "devmode disable"),
    ])
    def test_ansible_command_refused_on_kubernetes(
            self, harness, monkeypatch, tmp_path, capsys, argv, label):
        self._register(tmp_path, "kubernetes")
        assert _run(monkeypatch, argv) == 1
        assert ("'canasta %s' is only supported on Compose instances" % label
                in capsys.readouterr().err)
        assert harness.ansible_calls == []

    def test_unresolved_instance_left_to_the_command(
            self, harness, monkeypatch, tmp_path):
        self._register(tmp_path, "kubernetes")
        assert _run(monkeypatch, ["devmode", "enable", "-i", "other"]) \
            == "exec"

    def test_note_is_appended(self):
        cmd_def = {"orchestrators": ["compose"],
                   "orchestrators_note": "Use X\n  instead."}
        assert canasta.orchestrator_refusal(
            cmd_def, "devmode_enable", "kubernetes") == (
            "Error: 'canasta devmode enable' is only supported on Compose "
            "instances. Use X instead.")
        assert canasta.orchestrator_refusal(
            cmd_def, "devmode_enable", None) is None
        assert canasta.orchestrator_refusal(
            {}, "devmode_enable", "kubernetes") is None


class TestKubernetesDeprecationWarning:
    """Every run that sets up or acts on Kubernetes warns that support is
    going away; Compose runs stay silent."""

    @staticmethod
    def _register(tmp_path, orchestrator):
        import json
        (tmp_path / "conf.json").write_text(json.dumps({"Instances": {
            "mysite": {"path": str(tmp_path / "mysite"), "host": "localhost",
                       "orchestrator": orchestrator},
        }}))

    @staticmethod
    def _warned(capsys):
        return (canasta.KUBERNETES_DEPRECATION_WARNING
                in capsys.readouterr().err)

    @pytest.mark.parametrize("orchestrator", ["kubernetes", "k8s"])
    def test_instance_command_on_kubernetes(
            self, harness, monkeypatch, tmp_path, capsys, orchestrator):
        self._register(tmp_path, orchestrator)
        _run(monkeypatch, ["restart", "-i", "mysite"])
        assert self._warned(capsys)

    def test_instance_command_on_compose(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "compose")
        _run(monkeypatch, ["restart", "-i", "mysite"])
        assert not self._warned(capsys)

    def test_instance_found_from_working_directory(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "kubernetes")
        (tmp_path / "mysite").mkdir()
        monkeypatch.setenv("CANASTA_HOST_PWD", str(tmp_path / "mysite"))
        _run(monkeypatch, ["restart"])
        assert self._warned(capsys)

    @pytest.mark.parametrize("orchestrator,warned", [
        ("k8s", True), ("kubernetes", True), ("compose", False)])
    def test_create(self, harness, monkeypatch, capsys, orchestrator, warned):
        _run(monkeypatch, ["create", "-i", "new", "-w", "main",
                           "-n", "example.com", "-o", orchestrator])
        assert self._warned(capsys) is warned

    @pytest.mark.parametrize("package,warned", [
        ("k8s-cp", True), ("k8s-worker", True), ("docker", False)])
    def test_install(self, harness, monkeypatch, capsys, package, warned):
        _run(monkeypatch, ["install", package, "-H", "node1"])
        assert self._warned(capsys) is warned

    @pytest.mark.parametrize("orchestrator,warned", [
        ("kubernetes", True), ("compose", False)])
    def test_command_across_instances(
            self, harness, monkeypatch, tmp_path, capsys, orchestrator,
            warned):
        self._register(tmp_path, orchestrator)
        _run(monkeypatch, ["list"])
        assert self._warned(capsys) is warned

    def test_kubernetes_only_command(self, harness, monkeypatch, capsys):
        _run(monkeypatch, ["storage", "list"])
        assert self._warned(capsys)

    def test_not_repeated_after_self_update(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "kubernetes")
        monkeypatch.setenv("CANASTA_SELF_UPDATED", "1")
        _run(monkeypatch, ["list"])
        assert not self._warned(capsys)
