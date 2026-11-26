"""gitops set-role and remove-host on Kubernetes push with the deploy key and
an unencrypted hosts.yaml, without the CLI version check."""

import os

from test_gitops_hosts_change import TASKS, _load


class TestWiring:
    def test_kubernetes_uses_the_deploy_key(self):
        tasks = _load(os.path.join(TASKS, "hosts_change_kubernetes.yml"))
        assert tasks[0]["vars"] == {"_hc_git_env": "{{ gitops_k8s_git_env }}",
                                    "_hc_encrypted": False}

    def test_no_version_check(self):
        with open(os.path.join(TASKS, "hosts_change_kubernetes.yml")) as f:
            assert "_check_cli_versions.yml" not in f.read()
