"""The enable "not installed" failure lists missing names comma-separated."""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ENABLE = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks",
                      "enable.yml")


def _fail_msg():
    with open(ENABLE) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in tasks
                if "ansible.builtin.fail" in t)["ansible.builtin.fail"]["msg"]


def test_missing_names_joined_with_commas():
    rendered = Templar(loader=DataLoader(), variables={
        "_item_type": "extensions",
        "exec_result": {"stdout": "Foo\nBar\n"},
    }).template(trust_as_template(_fail_msg()))
    assert rendered == "Extensions not installed in the container: Foo, Bar."
