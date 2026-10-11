"""gitops add refuses to capture config/wikis.yaml before a pulled commit
is applied, since the live file still holds the previous values."""

import os

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ADD = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "add.yml")


def _names():
    with open(ADD) as f:
        return [t.get("name") for t in yaml.safe_load(f)]


def test_pending_check_precedes_the_capture():
    names = _names()
    check = names.index("Check for a pull still to be applied")
    refuse = names.index(
        "Refuse to capture config/wikis.yaml before a pulled commit is applied")
    capture = names.index("Capture wikis.yaml edits into the gitops template")
    assert check < refuse < capture


def test_refusal_is_gated_on_the_pending_apply():
    with open(ADD) as f:
        tasks = {t.get("name"): t for t in yaml.safe_load(f)}
    refuse = tasks[
        "Refuse to capture config/wikis.yaml before a pulled commit is applied"]
    assert "_gitops_apply_pending | bool" in refuse["when"]
