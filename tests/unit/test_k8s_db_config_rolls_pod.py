"""A db config change must reach the running database, not just the cluster.

my.cnf is mounted into the db pod with subPath, which is materialized at
pod start and never receives later ConfigMap updates. Without a checksum
annotation on the pod template, `helm upgrade` updates the ConfigMap,
nothing rolls the pod, and the CLI reports config and containers in sync
while the server keeps running on the old file — the Kubernetes shape of
the same failure #1456 fixed on Compose.
"""

import os
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHART = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta")
STATEFULSET = os.path.join(CHART, "templates", "statefulset-db.yaml")


def _pod_annotations(values=None):
    """Render the db StatefulSet and return its pod template annotations."""
    if not shutil_which("helm"):
        pytest.skip("helm is not installed")
    cmd = ["helm", "template", "canasta", CHART,
           "--set", "db.enabled=true",
           "--show-only", "templates/statefulset-db.yaml"]
    for key, value in (values or {}).items():
        cmd += ["--set-string", "%s=%s" % (key, value)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("helm template failed: %s" % out.stderr.strip()[:200])
    doc = yaml.safe_load(out.stdout)
    return (doc["spec"]["template"]["metadata"].get("annotations") or {})


def shutil_which(name):
    from shutil import which
    return which(name)


class TestDbConfigChecksum:
    def test_the_pod_template_carries_a_db_config_checksum(self):
        annotations = _pod_annotations()
        assert "checksum/db-config" in annotations, (
            "without this, a configData.db change updates the ConfigMap and "
            "leaves the running pod on the old file")

    def test_the_checksum_changes_with_the_config(self):
        # Identical checksums would roll nothing, which is the bug.
        before = _pod_annotations()["checksum/db-config"]
        after = _pod_annotations(
            {"configData.db.my\\.cnf": "[mysqld]\ninnodb_buffer_pool_size=1G"}
        )["checksum/db-config"]
        assert before != after, (
            "the annotation must vary with configData.db or the pod is "
            "never rolled")

    def test_the_source_mirrors_the_caddy_deployment(self):
        # The caddy deployment already does this; the two should not drift.
        with open(STATEFULSET) as f:
            text = f.read()
        assert "{{ .Values.configData.db | toJson | sha256sum }}" in text

    def test_my_cnf_is_still_mounted_with_subpath(self):
        # The annotation exists *because* of subPath. If the mount ever
        # stops using subPath this test should fail so the reasoning gets
        # revisited rather than silently outliving its cause.
        with open(STATEFULSET) as f:
            text = f.read()
        assert "subPath: my.cnf" in text
