"""import, add -d and create -d say whether the wiki's schema was updated.

The completion message used to read the same whether update.php ran or
was skipped with --skip-update, so after importing a dump from an older
MediaWiki the operator could not tell whether the schema was upgraded.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
IMPORT = os.path.join(REPO_ROOT, "playbooks", "import.yml")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
CREATE = os.path.join(REPO_ROOT, "roles", "create", "tasks", "main.yml")

UPDATED = "Its schema was updated."
SKIPPED = "Its schema was not updated (--skip-update)."
TIMED_OUT = "Its schema update had not finished when the wait ended."


def _msg(path, name, **variables):
    with open(path) as f:
        task = next(t for t in yaml.safe_load(f) if t.get("name") == name)
    templar = Templar(loader=DataLoader(), variables=variables)
    text = templar.template(trust_as_template(
        task["ansible.builtin.debug"]["msg"]))
    return " ".join(str(text).split())


def _import(**v):
    return _msg(IMPORT, "Report import complete", wiki="main",
                _import_extra_dumps={}, **v)


def test_import_reports_the_schema_update():
    assert _import() == "Database imported into wiki 'main'. " + UPDATED


def test_import_reports_a_skipped_update():
    assert _import(skip_update=True) == (
        "Database imported into wiki 'main'. " + SKIPPED)


def test_import_reports_an_update_still_running():
    assert _import(_rx_timed_out=True).endswith(TIMED_OUT)


def test_import_with_extra_databases_still_reports_it():
    assert _msg(IMPORT, "Report import complete", wiki="main",
                _import_extra_dumps={"main_cargo": "/d/m.main_cargo.sql"}) == (
        "Database imported into wiki 'main', with extra database(s) "
        "main_cargo. " + UPDATED)


def _add(**v):
    return _msg(ADD, "Report wiki added", wiki="second", instance_id="site",
                **v)


def test_add_from_a_dump_reports_the_schema_update():
    assert _add(database="/d/x.sql") == (
        "Wiki 'second' added to instance 'site'. " + UPDATED)
    assert _add(database="/d/x.sql", _rx_timed_out=True).endswith(TIMED_OUT)


def test_add_without_a_dump_says_nothing_about_the_schema():
    assert _add() == "Wiki 'second' added to instance 'site'."


def test_create_from_a_dump_reports_the_schema_update():
    with open(CREATE) as f:
        task = next(t for t in yaml.safe_load(f)
                    if t.get("name") == "Report the imported wiki's schema update")
    assert "database is defined and database != ''" in task["when"]
    assert _msg(CREATE, "Report the imported wiki's schema update",
                wiki="main") == "Database imported into wiki 'main'. " + UPDATED
