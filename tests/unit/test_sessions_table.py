"""`create` and `import` create the table the image keeps sessions in, for
databases installed or imported while the web container keeps running."""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASK_FILE = os.path.join(REPO_ROOT, "roles", "mediawiki", "tasks", "create_sessions_table.yml")
IMPORT = os.path.join(REPO_ROOT, "playbooks", "import.yml")
CREATE = os.path.join(REPO_ROOT, "roles", "create", "tasks", "main.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _task(tasks, name):
    return next(t for t in tasks if t.get("name") == name)


def _render(expr, variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _exec_task():
    return _task(_load(TASK_FILE), "Create the sessions table for each wiki")


def test_runs_the_image_script_as_www_data_and_skips_older_images():
    task = _exec_task()
    assert task["ansible.builtin.include_tasks"].endswith("roles/orchestrator/tasks/exec.yml")
    assert task["vars"]["exec_service"] == "web"
    assert task["vars"]["exec_user"] == "www-data"
    command = _render(task["vars"]["exec_command"], {"_sessions_table_wiki": "main"})
    assert command.strip() == (
        "if [ -f maintenance/createSessionsTable.php ]; then "
        "php maintenance/createSessionsTable.php --wiki=main; fi"
    )


def test_given_wikis_or_every_wiki():
    loop = _exec_task()["loop"]
    assert _render(loop, {"sessions_table_wikis": ["a"]}) == ["a"]
    assert _render(loop, {"_sessions_table_all_wikis": {"wiki_ids": ["a", "b"]}}) == ["a", "b"]


def test_import_creates_it_before_update_for_the_imported_wiki():
    tasks = _load(IMPORT)
    names = [t.get("name") for t in tasks]
    step = names.index("Create the imported wiki's sessions table")
    assert step > names.index("Import database")
    assert step > names.index("Copy per-wiki Settings.php if provided")
    assert step < names.index("Run update.php for the imported wiki")
    task = tasks[step]
    assert task["ansible.builtin.include_role"]["tasks_from"] == "create_sessions_table.yml"
    assert "when" not in task
    assert [_render(w, {"wiki": "main"}) for w in task["vars"]["sessions_table_wikis"]] == ["main"]


def _create_step():
    tasks = _load(CREATE)
    names = [t.get("name") for t in tasks]
    step = names.index("Create the sessions table for each wiki")
    assert step > names.index("Start containers with full config")
    assert step < names.index("Run update.php for the imported wiki")
    return tasks[step]


def test_create_covers_every_installed_wiki():
    task = _create_step()
    variables = {"_created_wikis": {"wiki_ids": ["main", "docs"]}, "wiki": "main"}
    assert _render(task["vars"]["sessions_table_wikis"], variables) == ["main", "docs"]
    assert _render("{{ %s }}" % task["when"], variables) is True


def test_create_from_a_dump_covers_only_the_imported_wiki():
    task = _create_step()
    variables = {"_created_wikis": {"wiki_ids": ["main", "docs"]}, "wiki": "main",
                 "database": "/tmp/dump.sql"}
    assert _render(task["vars"]["sessions_table_wikis"], variables) == ["main"]


def test_create_skip_install_without_a_dump_skips_it():
    task = _create_step()
    assert _render("{{ %s }}" % task["when"], {"skip_install": True}) is False
