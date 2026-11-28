"""`canasta add` and `canasta remove` restart a gitops-managed
instance and stage the wiki in its gitops repo."""

from _add_remove_play import _add, _remove


def test_add_restarts(tmp_path):
    out, lines, _ = _add(tmp_path)
    assert "RESTART" in lines
    assert "STAGE" in lines
    assert "Wiki 'docs' added to instance 'inst'." in out
    assert "gitops push -i" not in out


def test_remove_restarts(tmp_path):
    out, lines, _ = _remove(tmp_path)
    assert "RESTART" in lines
    assert "UNSTAGE" in lines
    assert "Wiki 'docs' removed from instance 'inst'." in out
    assert "staged in its gitops repo" not in out
