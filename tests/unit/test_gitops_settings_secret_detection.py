"""The gitops push path warns when a committed Settings.php looks like it holds
an inline secret, steering the operator to `config set --secret`. It must be
warn-only by default (fail only in strict mode) and never print the value."""
import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DET = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                   "_detect_settings_secrets.yml")
PUSH = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push.yml")


def _read(p):
    with open(p) as f:
        return f.read()


def _walk(tasks):
    for t in tasks:
        yield t
        if isinstance(t, dict) and "block" in t:
            yield from _walk(t["block"])


def test_push_runs_detection():
    assert "_detect_settings_secrets.yml" in _read(PUSH)


def test_detection_reports_files_only_not_values():
    # grep -l lists matching files without emitting the matched line/value.
    assert "grep -rlEi" in _read(DET)


def test_detection_warns_and_only_fails_when_strict():
    tasks = list(_walk(yaml.safe_load(_read(DET))))
    fails = [t for t in tasks if "ansible.builtin.fail" in t]
    assert fails, "must have a strict-mode fail"
    for f in fails:
        assert "gitops_strict_secrets" in str(f.get("when", "")), (
            "the fail must be gated on strict mode; default is warn-only"
        )
    assert [t for t in tasks if "ansible.builtin.debug" in t], "must warn"
