"""`canasta import` runs update.php for the imported wiki, after the dump and
any replacement Settings.php are in place, unless --skip-update is given."""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
IMPORT = os.path.join(REPO_ROOT, "playbooks", "import.yml")
UPDATE_TASK = "Run update.php for the imported wiki"


def _tasks():
    with open(IMPORT) as f:
        return yaml.safe_load(f)


def _names():
    return [t.get("name") for t in _tasks()]


def _update_task():
    return next(t for t in _tasks() if t.get("name") == UPDATE_TASK)


def _render(expr, variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _runs(variables):
    return _render("{{ %s }}" % _update_task()["when"], variables)


def test_update_runs_after_the_imports_and_settings_copy():
    names = _names()
    update = names.index(UPDATE_TASK)
    assert update > names.index("Import database")
    assert update > names.index("Import each extra database")
    assert update > names.index("Copy per-wiki Settings.php if provided")


def test_update_targets_the_imported_wiki_in_web():
    task = _update_task()
    assert task["ansible.builtin.include_tasks"].endswith(
        "roles/orchestrator/tasks/exec.yml")
    assert task["vars"]["exec_service"] == "web"
    command = _render(task["vars"]["exec_command"], {"wiki": "main"})
    assert command == "php maintenance/update.php --wiki=main"


def test_update_runs_by_default():
    assert _runs({}) is True


def test_skip_update_skips_it():
    assert _runs({"skip_update": True}) is False
