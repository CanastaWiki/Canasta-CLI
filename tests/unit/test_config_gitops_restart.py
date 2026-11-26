"""`canasta config set` and `config unset` restart a gitops-managed Compose
instance and record the change in its host vars."""

from _config_play import _set, _unset


def test_set_restarts_and_updates_the_host_vars(tmp_path):
    out, lines, _ = _set(tmp_path, k8s=False)
    assert "RESTART" in lines
    assert "UPDATE_GITOPS_VARS" in lines
    assert "staged in its gitops repo" not in out


def test_unset_restarts(tmp_path):
    out, lines, _ = _unset(tmp_path, k8s=False)
    assert "RESTART" in lines
    assert "staged in its gitops repo" not in out
