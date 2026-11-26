"""MediaWiki logs persist across web container restarts on both orchestrators.

The image keeps its logs in /var/log/mediawiki only when something is
mounted there; otherwise it links the directory into the container's own
filesystem, and a pod restart loses them.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ORCH = os.path.join(REPO_ROOT, "roles", "orchestrator")
HELM = os.path.join(ORCH, "files", "helm", "canasta")
LOG_DIR = "/var/log/mediawiki"


def _read(*parts):
    with open(os.path.join(*parts)) as f:
        return f.read()


def test_k8s_web_mounts_the_log_pvc():
    web = _read(HELM, "templates", "deployment-web.yaml")
    assert ("- name: mediawiki-logs\n              mountPath: " + LOG_DIR
            in web)
    assert ("- name: mediawiki-logs\n          persistentVolumeClaim:\n"
            '            claimName: {{ include "canasta.fullname" . }}'
            "-mediawiki-logs" in web)


def test_k8s_log_pvc_is_defined():
    pvc = _read(HELM, "templates", "pvc-mediawiki-logs.yaml")
    assert '{{ include "canasta.fullname" . }}-mediawiki-logs' in pvc
    values = yaml.safe_load(_read(HELM, "values.yaml"))
    assert values["persistence"]["logs"]["size"]


def test_k8s_log_pvc_follows_the_instance_storage_settings():
    values = _read(ORCH, "templates", "k8s_values.yaml.j2")
    assert "  logs:\n{% if storage_class" in values
