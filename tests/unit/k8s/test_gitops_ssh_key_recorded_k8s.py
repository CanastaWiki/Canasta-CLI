"""On Kubernetes, gitops commands keep authenticating with the deploy key
init generated rather than a recorded --ssh-key, and a recorded key takes
precedence over the deploy key for status and diff."""

from test_gitops_ssh_key_recorded import _git_ssh_command, _play


class TestTheKeyIsRecordedAndRecalled:
    def test_kubernetes_keeps_its_deploy_key(self, tmp_path):
        # Its later commands authenticate with the generated deploy key,
        # which Argo CD uses too.
        _play(tmp_path, tmp_path, {"ssh_key": "/home/u/.ssh/op"}, k8s=True)
        assert not (tmp_path / ".gitops-ssh-key").exists()
        (tmp_path / ".gitops-ssh-key").write_text("/home/u/.ssh/op\n")
        assert "KEY=[]" in _play(tmp_path, tmp_path, k8s=True)


class TestStatusAndDiffUseIt:
    def test_it_comes_before_the_kubernetes_deploy_key(self, tmp_path):
        (tmp_path / ".gitops-ssh-key").write_text("/home/u/.ssh/deploy\n")
        (tmp_path / ".gitops-deploy-key").write_text("k")
        assert "/home/u/.ssh/deploy" in _git_ssh_command(tmp_path)
