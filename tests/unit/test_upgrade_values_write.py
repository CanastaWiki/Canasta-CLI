"""`canasta upgrade` leaves a Kubernetes values.yaml alone unless it changes.

values.yaml is tracked by gitops, so a rewrite that only reformats it leaves
the repo dirty. The write must use the same indent as every other writer and
must be skipped when the bumped values equal the current ones.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TAG = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml")


def _named(tasks, name):
    for t in tasks:
        if t.get("name") == name:
            return t
        found = _named(t.get("block", []), name) if "block" in t else None
        if found:
            return found
    return None


def _write_task():
    with open(TAG) as f:
        return _named(yaml.safe_load(f), "Write updated values.yaml")


def test_write_uses_two_space_indent():
    content = _write_task()["ansible.builtin.copy"]["content"]
    assert "to_nice_yaml(indent=2)" in content


@pytest.mark.parametrize("old_tag, new_tag, writes", [
    ("3.5.24", "3.5.24", False),
    ("3.5.23", "3.5.24", True),
])
def test_write_only_when_values_change(old_tag, new_tag, writes):
    when = _write_task()["when"]
    current = {"image": {"repository": "ghcr.io/canastawiki/canasta",
                         "tag": old_tag},
               "domains": ["example.com"]}
    new = {"image": {"repository": "ghcr.io/canastawiki/canasta",
                     "tag": new_tag},
           "domains": ["example.com"]}
    templar = Templar(loader=DataLoader(), variables={
        "_upgrade_values": current,
        "_upgrade_new_values": new,
    })
    result = templar.template(trust_as_template("{{ " + when + " }}"))
    assert str(result).strip() == str(writes)
