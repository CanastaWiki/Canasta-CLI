"""`canasta upgrade` restarts a Kubernetes build_from instance only for a changed image.

The rebuilt image is still pushed to the in-cluster registry, and the push
digest is compared with the images the web pods run, with docker and
kubectl stubbed. A changed Helm chart also triggers the restart.
"""

import pytest

from test_upgrade_restart_on_rebuilt_image import (
    NEW,
    OLD,
    _publish,
    _restart_needed,
)


def _pods(*digests):
    return " ".join("10.43.0.2:5000/canasta@" + d for d in digests)


class TestKubernetes:
    def test_an_unchanged_rebuild_does_not_restart(self, tmp_path):
        assert _publish(tmp_path, True, pushed=NEW, pods=_pods(NEW)) is False

    def test_a_changed_rebuild_restarts(self, tmp_path):
        assert _publish(tmp_path, True, pushed=NEW, pods=_pods(OLD)) is True

    def test_any_pod_on_the_old_image_restarts(self, tmp_path):
        assert _publish(
            tmp_path, True, pushed=NEW, pods=_pods(NEW, OLD)) is True

    def test_no_running_pod_counts_as_changed(self, tmp_path):
        assert _publish(tmp_path, True, pushed=NEW, pods="") is True

    def test_an_unread_push_digest_counts_as_changed(self, tmp_path):
        assert _publish(tmp_path, True, pushed="", pods=_pods(NEW)) is True

    def test_the_image_is_still_pushed(self, tmp_path):
        _publish(tmp_path, True, pushed=NEW, pods=_pods(NEW))
        calls = (tmp_path / "calls.log").read_text()
        assert "push localhost:5000/canasta:local" in calls


@pytest.mark.parametrize("overrides, expected", [
    ({"_k8s_chart_changed": True}, True),
])
def test_a_rebuild_alone_does_not_restart(overrides, expected):
    assert _restart_needed(**overrides) is expected
