"""The paused-instance refusal does not check a Kubernetes instance, which
has no paused containers to find."""

from test_paused_instance_commands import _refuse


class TestRefusePaused:
    def test_kubernetes_is_not_checked(self, tmp_path):
        proc = _refuse(tmp_path, "c1 web\\n", k8s=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
