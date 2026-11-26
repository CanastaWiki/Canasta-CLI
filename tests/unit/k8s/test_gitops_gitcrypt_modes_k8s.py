"""git-crypt modes on Kubernetes gitops.

Kubernetes init rejects --git-crypt-gpg-user before touching an existing
repo, and Kubernetes join requires --key.
"""

from test_gitops_gitcrypt_modes import _load, _named


class TestInitKeyOptionalInGpgMode:
    def test_kubernetes_rejects_gpg_user_before_reinit(self):
        tasks = _load("init.yml")
        names = [t.get("name") for t in tasks]
        guard = "Reject --git-crypt-gpg-user on Kubernetes"
        assert names.index(guard) < names.index(
            "Reinit — move existing .git aside before re-init")
        when = " ".join(_named(tasks, guard)["when"])
        assert "instance_is_k8s | bool" in when and "git_crypt_gpg_user" in when


class TestJoinUnlockChoice:
    def test_kubernetes_join_requires_key(self):
        task = _named(_load("join_kubernetes.yml"),
                      "Require --key (the SSH deploy key basename)")
        assert "ansible.builtin.fail" in task
