"""`canasta upgrade` rolls Kubernetes pods onto a changed image tag.

The restart decision used only `_pull_changed`, which the pull step sets on
Compose; nothing pulls on Kubernetes, so a bumped values.yaml tag was never
deployed. The facts also persisted across the instances of one run.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TAG = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml")
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _named(tasks, name):
    for t in tasks:
        if t.get("name") == name:
            return t
        found = _named(t.get("block", []), name) if "block" in t else None
        if found:
            return found
    return None


@pytest.mark.parametrize("old, new, changed", [
    ("3.5.23", "3.5.24", True),
    ("3.5.24", "3.5.24", False),
    ("", "3.5.24", True),
])
def test_tag_change_is_detected(old, new, changed):
    task = _named(_load(TAG), "Record whether the image tag changed")
    expr = task["ansible.builtin.set_fact"]["_k8s_image_tag_changed"]
    templar = Templar(loader=DataLoader(), variables={
        "_upgrade_values": {"image": {"tag": old}},
        "_upgrade_new_values": {"image": {"tag": new}},
    })
    assert str(templar.template(trust_as_template(expr))).strip() == str(changed)


def test_signals_are_reset_before_the_bump_and_feed_the_restart():
    tasks = _load(MAIN)
    names = [t.get("name") for t in tasks]
    reset = tasks[names.index("Reset image change signals for this instance")]
    assert reset["ansible.builtin.set_fact"] == {
        "_pull_changed": False, "_k8s_image_tag_changed": False}
    assert names.index("Reset image change signals for this instance") < names.index(
        "Update configured image tag") < names.index("Check if new images were pulled")
    updated = tasks[names.index("Check if new images were pulled")][
        "ansible.builtin.set_fact"]["_images_updated"]
    assert "_k8s_image_tag_changed" in updated and "_pull_changed" in updated
