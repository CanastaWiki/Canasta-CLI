"""The CI coverage check must see every way a workflow names its tests.

The GitOps job passes its list as a string through `sg ... -c`, which the
check once ignored, so it reported tests CI does run as never run.
"""

import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import validate_ci_test_coverage as coverage  # noqa: E402


def _named(tmp_path, monkeypatch, workflow):
    (tmp_path / "ci.yml").write_text(workflow)
    monkeypatch.setattr(coverage, "WORKFLOWS", str(tmp_path))
    return coverage.tests_named_by_ci()


def test_bash_array_lists(tmp_path, monkeypatch):
    named = _named(tmp_path, monkeypatch,
                   '        "a") tests=(backup reconcile) ;;\n')
    assert {"backup", "reconcile"} <= named


def test_string_lists_including_continuations(tmp_path, monkeypatch):
    named = _named(tmp_path, monkeypatch,
                   '        "a")\n'
                   '          tests="gitops-add-wiki-tracked gitops-status-fetch"\n'
                   '          tests="$tests gitops-fix-submodules" ;;\n')
    assert {"gitops-add-wiki-tracked", "gitops-status-fetch",
            "gitops-fix-submodules"} <= named


def test_every_registered_test_is_run_or_exempt():
    missing = (coverage.registered_tests() - coverage.tests_named_by_ci()
               - set(coverage.NOT_IN_CI))
    assert not missing, sorted(missing)
