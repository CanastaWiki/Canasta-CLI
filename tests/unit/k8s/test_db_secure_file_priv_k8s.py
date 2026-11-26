"""The Kubernetes database StatefulSet confines MariaDB file I/O.

The db container passes --secure-file-priv=/var/lib/mysql-files and mounts
an emptyDir there, so the directory exists and starts empty.
"""

import os

from test_db_secure_file_priv import FILES_DIR, REPO_ROOT


STATEFULSET = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta",
    "templates", "statefulset-db.yaml")


def test_k8s_db_sets_the_flag_and_mounts_an_empty_directory():
    with open(STATEFULSET) as f:
        text = f.read()
    assert "- --secure-file-priv=%s\n" % FILES_DIR in text
    assert "- name: db-files\n              mountPath: %s\n" % FILES_DIR in text
    assert "- name: db-files\n          emptyDir: {}\n" in text
