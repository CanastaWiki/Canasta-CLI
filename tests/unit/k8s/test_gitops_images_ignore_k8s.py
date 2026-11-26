"""The upgrade's gitops .gitignore refresh on a Kubernetes instance.

It replaces the unanchored images/ rule older repositories carry with the
anchored one, keeps the operator's own rules, and is idempotent.
"""

import pytest

from test_gitops_images_ignore import SHIPPED, UPLOAD, _ignored, _instance, _refresh


@pytest.mark.parametrize("k8s", [True])
def test_refresh_replaces_the_unanchored_rule(tmp_path, k8s):
    inst = _instance(tmp_path, ".env\nimages/\nmy-own-rule\n")
    _refresh(tmp_path, inst, k8s)
    lines = (inst / ".gitignore").read_text().splitlines()
    assert "images/" not in lines
    assert lines.count("/images/") == 1
    assert "my-own-rule" in lines
    assert _ignored(inst, UPLOAD)
    for path in SHIPPED:
        assert not _ignored(inst, path), path
    before = (inst / ".gitignore").read_text()
    _refresh(tmp_path, inst, k8s)
    assert (inst / ".gitignore").read_text() == before
