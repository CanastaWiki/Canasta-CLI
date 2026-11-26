"""A Kubernetes full restore keeps database dumps and the Secrets manifest off the host.

The restored config is copied to the host without config/backup, and the
dumps and Secrets manifest are read from the restore pod instead.
"""

import os

import yaml

from _k8s_restore_copy import resolve_copy_task
from test_gitops_secret_file_modes import REPO_ROOT, _load, _walk


RESTORE_K8S = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_restore_instance.yml")


class TestK8sRestoreKeepsDumpsOffHost:
    def test_full_restore_does_not_copy_config_backup(self):
        task = resolve_copy_task(next(
            t for t in _load(RESTORE_K8S)
            if t.get("name") == "Copy restored config to host"))
        cmd = task["ansible.builtin.shell"]["cmd"]
        assert "tar xf - --exclude=./backup" in cmd

    def test_dumps_and_secrets_are_read_from_the_pod(self):
        tasks = list(_walk(_load(RESTORE_K8S)))
        text = yaml.safe_dump(tasks)
        assert "instance_path }}/config/backup" not in text
        assert "/currentsnapshot/config/backup/{{ item }}" in text
        assert "/currentsnapshot/config/backup/{{ _restore_secrets_filename }}" in text
