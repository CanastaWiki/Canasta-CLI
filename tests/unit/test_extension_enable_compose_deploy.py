"""`canasta extension|skin enable|disable` on a Compose instance, gitops-managed
or not, commits the settings change, applies it, and runs update.php after an
enable."""

import pytest

from _extension_play import DISABLE, ENABLE, _run


@pytest.mark.parametrize("gitops", [True, False])
def test_enable_applies_and_runs_update(tmp_path, gitops):
    out, lines = _run(tmp_path, ENABLE, k8s=False, gitops=gitops, wiki="docs")
    assert lines == ["COMMIT Enable", "APPLY", "UPDATE docs"]
    assert "Enabled extensions: Cite,Foo" in out
    assert "gitops push -i" not in out


def test_enable_skip_update_skips_update_php(tmp_path):
    _, lines = _run(tmp_path, ENABLE, k8s=False, gitops=False, skip_update=True)
    assert lines == ["COMMIT Enable", "APPLY"]


def test_disable_on_a_gitops_instance_applies(tmp_path):
    out, lines = _run(tmp_path, DISABLE, k8s=False)
    assert lines == ["COMMIT Disable", "APPLY"]
    assert "Disabled extensions: Cite,Foo" in out
    assert "gitops push -i" not in out
