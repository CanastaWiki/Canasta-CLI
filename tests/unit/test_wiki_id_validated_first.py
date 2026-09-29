"""remove/import/export validate the wiki ID before using it.

Each builds paths and SQL from the ID; `remove` deleted directories built
from it before checking anything. `remove` also checks the wiki exists and
is not the last one before its first destructive step.
"""

import os
import sys

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PLAYBOOKS = os.path.join(REPO_ROOT, "playbooks")
sys.path.insert(0, os.path.join(REPO_ROOT, "roles", "common", "module_utils"))

from canasta_validate import validate_wiki_id  # noqa: E402

EXISTS_CHECK = "Require an existing wiki that is not the last one"


def _tasks(name):
    with open(os.path.join(PLAYBOOKS, name)) as f:
        return yaml.safe_load(f)


def _names(tasks):
    return [t.get("name") for t in tasks]


@pytest.mark.parametrize("playbook", ["remove.yml", "import.yml", "export.yml"])
def test_wiki_id_is_validated_right_after_resolving_the_instance(playbook):
    tasks = _tasks(playbook)
    assert _names(tasks)[:2] == ["Resolve instance", "Validate wiki ID"]
    assert tasks[1]["ansible.builtin.include_tasks"].strip().endswith(
        "roles/common/tasks/validate_wiki_id.yml")


@pytest.mark.parametrize("wiki_id", ["../config", "a/b", "x'y", "a b", ""])
def test_validator_rejects_path_and_quote_characters(wiki_id):
    assert validate_wiki_id(wiki_id) is not None


def test_remove_checks_the_wiki_before_deleting_anything():
    tasks = _tasks("remove.yml")
    names = _names(tasks)
    check = names.index(EXISTS_CHECK)
    first_delete = min(
        i for i, t in enumerate(tasks)
        if "exec.yml" in str(t.get("ansible.builtin.include_tasks", ""))
        or "canasta_wikis_yaml" in t and t["canasta_wikis_yaml"].get("state") == "remove"
        or (t.get("ansible.builtin.file") or {}).get("state") == "absent"
    )
    assert check < first_delete
    assert names.index("Require confirmation") < first_delete


def _render(wiki, wiki_ids):
    task = next(t for t in _tasks("remove.yml") if t.get("name") == EXISTS_CHECK)
    templar = Templar(loader=DataLoader(), variables={
        "wiki": wiki, "_remove_wikis": {"wiki_ids": wiki_ids}})
    fails = templar.template(trust_as_template("{{ %s }}" % task["when"]))
    msg = templar.template(trust_as_template(task["ansible.builtin.fail"]["msg"]))
    return fails, msg


@pytest.mark.parametrize("wiki, wiki_ids, expected", [
    ("docs", ["main", "docs"], None),
    ("other", ["main", "docs"], "Wiki ID 'other' not found"),
    ("main", ["main"], "Cannot remove the last wiki"),
])
def test_remove_existence_check(wiki, wiki_ids, expected):
    fails, msg = _render(wiki, wiki_ids)
    assert fails is (expected is not None)
    if expected:
        assert msg == expected


def test_remove_quotes_the_database_as_an_identifier():
    drop = next(t for t in _tasks("remove.yml")
                if t.get("name") == "Drop wiki database")
    command = drop["vars"]["exec_command"]
    assert "DROP DATABASE IF EXISTS `{{ wiki }}`" in command
    assert "quote" not in command.split("DROP", 1)[1]
