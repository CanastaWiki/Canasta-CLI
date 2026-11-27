"""canasta.py main() for Kubernetes instances: refusal of Compose-only
commands on a Kubernetes instance, and the refusal of every run that sets
up or acts on Kubernetes."""

import pytest

import test_canasta_main
from test_canasta_main import _run, canasta

harness = test_canasta_main.harness


class TestCommandOrchestrators:
    @staticmethod
    def _register(tmp_path, orchestrator):
        import json
        (tmp_path / "conf.json").write_text(json.dumps({"Instances": {
            "mysite": {"path": str(tmp_path / "mysite"), "host": "localhost",
                       "orchestrator": orchestrator},
        }}))

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


class TestKubernetesRefused:
    """5.x runs on Docker Compose only: every run that sets up or acts on
    Kubernetes is refused before it does anything; Compose runs go ahead."""

    @staticmethod
    def _register(tmp_path, orchestrator):
        import json
        (tmp_path / "conf.json").write_text(json.dumps({"Instances": {
            "mysite": {"path": str(tmp_path / "mysite"), "host": "localhost",
                       "orchestrator": orchestrator},
        }}))

    @staticmethod
    def _refused(harness, capsys, rc):
        refused = canasta.KUBERNETES_REFUSAL in capsys.readouterr().err
        if refused:
            assert rc == 1
            assert harness.ansible_calls == [] and harness.direct_calls == []
        return refused

    @pytest.mark.parametrize("orchestrator", ["kubernetes", "k8s"])
    def test_instance_command_on_kubernetes(
            self, harness, monkeypatch, tmp_path, capsys, orchestrator):
        self._register(tmp_path, orchestrator)
        rc = _run(monkeypatch, ["restart", "-i", "mysite"])
        assert self._refused(harness, capsys, rc)

    def test_instance_command_on_compose(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "compose")
        rc = _run(monkeypatch, ["restart", "-i", "mysite"])
        assert not self._refused(harness, capsys, rc)

    def test_instance_found_from_working_directory(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "kubernetes")
        (tmp_path / "mysite").mkdir()
        monkeypatch.setenv("CANASTA_HOST_PWD", str(tmp_path / "mysite"))
        rc = _run(monkeypatch, ["restart"])
        assert self._refused(harness, capsys, rc)

    @pytest.mark.parametrize("orchestrator,refused", [
        ("k8s", True), ("kubernetes", True), ("compose", False)])
    def test_create(self, harness, monkeypatch, capsys, orchestrator, refused):
        rc = _run(monkeypatch, ["create", "-i", "new", "-w", "main",
                                "-n", "example.com", "-o", orchestrator])
        assert self._refused(harness, capsys, rc) is refused

    @pytest.mark.parametrize("orchestrator,refused", [
        ("kubernetes", True), ("compose", False)])
    def test_upgrade_across_instances(
            self, harness, monkeypatch, tmp_path, capsys, orchestrator,
            refused):
        self._register(tmp_path, orchestrator)
        monkeypatch.setattr(canasta, "self_update_cli", lambda **kw: None)
        rc = _run(monkeypatch, ["upgrade"])
        assert self._refused(harness, capsys, rc) is refused

    def test_list_runs_with_a_kubernetes_instance(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "kubernetes")
        rc = _run(monkeypatch, ["list"])
        assert not self._refused(harness, capsys, rc)
