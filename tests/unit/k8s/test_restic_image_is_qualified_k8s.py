"""The Kubernetes backup and restore Jobs name restic by its pinned,
registry-qualified image.

A CRI-O cluster, like Podman, refuses the short name `restic/restic`;
containerd assumes docker.io, so k3s accepts it either way.
"""

import os

import pytest

from test_restic_image_is_qualified import (
    _check_no_bare_short_name, _check_site_uses_the_pinned_image)

SITES = (
    os.path.join("roles", "orchestrator", "tasks", "k8s_restore_instance.yml"),
    os.path.join("roles", "orchestrator", "tasks", "k8s_run_backup.yml"),
)


class TestEveryResticReferenceIsQualified:
    @pytest.mark.parametrize("rel", SITES)
    def test_no_bare_short_name(self, rel):
        _check_no_bare_short_name(rel)

    @pytest.mark.parametrize("rel", SITES)
    def test_every_site_uses_the_pinned_image(self, rel):
        _check_site_uses_the_pinned_image(rel)
