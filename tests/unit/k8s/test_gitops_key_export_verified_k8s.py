"""Kubernetes `gitops init` verifies the key files it exports (the SSH key,
and the age key when secrets are encrypted) before reporting success, with
advice that does not mention git-crypt."""

from test_gitops_key_export_verified import INIT_K8S, _index_of, _tasks


def _k8s_verifications():
    return {
        str(t.get("vars", {}).get("_exported_key_path")): t
        for t in _tasks(INIT_K8S)
        if "_verify_exported_key.yml" in str(
            t.get("ansible.builtin.include_tasks", ""))
    }


def _k8s_report():
    return next(t for t in _tasks(INIT_K8S)
                if t.get("name") == "Report gitops initialized")


class TestKubernetesInit:
    def test_only_the_files_kubernetes_writes_are_verified(self):
        # Kubernetes init writes <key>.ssh and, with --encrypt-secrets,
        # <key>.age. Nothing writes <key> itself, so checking it fails
        # every init after the work is done.
        assert set(_k8s_verifications()) == {
            "{{ _ssh_key_path }}", "{{ key }}.age"}

    def test_the_age_key_is_verified_only_when_encryption_is_on(self):
        ssh = _k8s_verifications()["{{ _ssh_key_path }}"]
        age = _k8s_verifications()["{{ key }}.age"]
        assert "when" not in ssh
        assert "gitops_sops_secrets" in str(age.get("when"))

    def test_no_git_crypt_wording_on_kubernetes(self):
        for task in _k8s_verifications().values():
            assert "git-crypt" not in str(task["vars"]).lower()
            assert "_exported_key_remedy" in task["vars"], (
                "the default advice is about the Compose git-crypt key")
        msg = _k8s_report()["ansible.builtin.debug"]["msg"]
        assert "git-crypt" not in msg.lower()
        assert "{{ _ssh_key_path }}" in msg
        assert "'.age'" in msg

    def test_canasta_docker_is_blamed_only_under_canasta_docker(self):
        with open(INIT_K8S) as f:
            text = f.read()
        assert "'CANASTA_RUN_MODE') == 'docker'" in text

    def test_verification_precedes_the_report(self):
        verify = _index_of(
            INIT_K8S,
            lambda t: "_verify_exported_key.yml" in str(
                t.get("ansible.builtin.include_tasks", "")))
        report = _index_of(
            INIT_K8S,
            lambda t: t.get("name") == "Report gitops initialized")
        assert verify != -1 and verify < report
