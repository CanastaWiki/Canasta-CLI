"""canasta.py main() refuses every run that acts on a Kubernetes instance a
4.x CLI created."""

import json

import pytest

import test_canasta_main
from test_canasta_main import _run, canasta

harness = test_canasta_main.harness


class TestKubernetesRefused:
    """5.x runs on Docker Compose only: every run that acts on a Kubernetes
    instance is refused before it does anything; Compose runs go ahead."""

    @staticmethod
    def _register(tmp_path, *orchestrators):
        (tmp_path / "conf.json").write_text(json.dumps({"Instances": {
            "site%d" % n: {"path": str(tmp_path / ("site%d" % n)),
                           "host": "localhost", "orchestrator": orchestrator}
            for n, orchestrator in enumerate(orchestrators)
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
        rc = _run(monkeypatch, ["restart", "-i", "site0"])
        assert self._refused(harness, capsys, rc)

    def test_instance_command_on_compose(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "compose")
        rc = _run(monkeypatch, ["restart", "-i", "site0"])
        assert not self._refused(harness, capsys, rc)

    def test_instance_found_from_working_directory(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "kubernetes")
        (tmp_path / "site0").mkdir()
        monkeypatch.setenv("CANASTA_HOST_PWD", str(tmp_path / "site0"))
        rc = _run(monkeypatch, ["restart"])
        assert self._refused(harness, capsys, rc)

    def test_unresolved_instance_left_to_the_command(
            self, harness, monkeypatch, tmp_path):
        self._register(tmp_path, "kubernetes")
        assert _run(monkeypatch, ["devmode", "enable", "-i", "other"]) \
            == "exec"

    def test_create_goes_ahead(self, harness, monkeypatch, capsys):
        rc = _run(monkeypatch, ["create", "-i", "new", "-w", "main",
                                "-n", "example.com"])
        assert not self._refused(harness, capsys, rc)

    @pytest.mark.parametrize("orchestrators,refused", [
        (("kubernetes",), True),
        (("k8s",), True),
        (("compose", "kubernetes"), True),
        (("compose",), False),
        ((), False),
    ])
    def test_upgrade_across_instances(
            self, harness, monkeypatch, tmp_path, capsys, orchestrators,
            refused):
        self._register(tmp_path, *orchestrators)
        monkeypatch.setattr(canasta, "self_update_cli", lambda **kw: None)
        rc = _run(monkeypatch, ["upgrade"])
        assert self._refused(harness, capsys, rc) is refused

    def test_list_runs_with_a_kubernetes_instance(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "kubernetes")
        rc = _run(monkeypatch, ["list"])
        assert not self._refused(harness, capsys, rc)
