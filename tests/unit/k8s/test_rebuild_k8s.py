"""`canasta rebuild` refuses a Kubernetes instance."""

from test_rebuild import _args, direct_commands


def _patch_k8s_inst(monkeypatch):
    monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
        lambda args: ("test", {
            "path": "/srv/test",
            "orchestrator": "k8s",
            "host": "localhost",
            "devMode": False,
        }),
    )


class TestRebuildK8sRefused:
    def test_k8s_instance_rejects_with_error(self, monkeypatch, capsys):
        _patch_k8s_inst(monkeypatch)
        rc = direct_commands.cmd_rebuild(_args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "Compose" in err
        assert "Kubernetes" in err
