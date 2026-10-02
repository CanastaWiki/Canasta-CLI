"""export/import carry a wiki's extra databases in sibling files.

`canasta export -w main` writes the wiki's own database to the output file
and each extra database it declares to <stem>.<database>.sql (or .sql.gz).
`canasta import -w main -d <file>` imports each declared extra database
from the same sibling, and reports declared databases with no dump and
dumps for databases the wiki does not declare.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar, trust_as_template

# The import step uses the fileglob lookup, which needs the plugin loader.
init_plugin_loader()

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
EXPORT = os.path.join(REPO_ROOT, "playbooks", "export.yml")
IMPORT = os.path.join(REPO_ROOT, "playbooks", "import.yml")


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _by_name(path, name):
    return next(t for t in _tasks(path) if t.get("name") == name)


def _render(expr, variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _export_dests(export_file, gzip, databases, wiki="main"):
    names = _by_name(EXPORT, "Name the sibling files for extra databases")
    variables = {"_export_file": export_file, "_export_gzip": gzip,
                 "wiki": wiki, "_export_wiki": {"databases": databases}}
    for key, expr in names["ansible.builtin.set_fact"].items():
        variables[key] = _render(expr, variables)
    loop = _by_name(EXPORT, "Export each of the wiki's databases")
    assert _render(loop["loop"], variables) == databases
    return {db: _render(loop["vars"]["export_dest"], {**variables, "item": db})
            for db in databases}


class TestExportFileNames:
    def test_main_database_keeps_the_given_name(self):
        dests = _export_dests("/w/main.sql", False, ["main", "main_cargo"])
        assert dests == {"main": "/w/main.sql",
                         "main_cargo": "/w/main.main_cargo.sql"}

    def test_gzip_carries_to_the_siblings(self):
        dests = _export_dests("/w/prod.sql.gz", True, ["main", "main_cargo"])
        assert dests["main_cargo"] == "/w/prod.main_cargo.sql.gz"

    def test_name_without_sql_extension(self):
        dests = _export_dests("/w/dump", False, ["main", "x"])
        assert dests == {"main": "/w/dump", "x": "/w/dump.x.sql"}

    def test_wiki_without_extra_databases(self):
        assert _export_dests("/w/main.sql", False, ["main"]) == {
            "main": "/w/main.sql"}


class TestImportFindsTheSiblings:
    def _run(self, tmp_path, files, declared):
        for name in files:
            (tmp_path / name).write_text("-- dump\n")
        variables = {"database": str(tmp_path / "main.sql"), "wiki": "main",
                     "_import_wiki": {"databases": ["main"] + declared}}
        names = _by_name(IMPORT, "Name where the extra databases' dumps would be")
        for key, expr in names["ansible.builtin.set_fact"].items():
            variables[key] = _render(expr, variables) if isinstance(expr, str) else expr
        find = _by_name(IMPORT, "Find the dump for each extra database")
        for item in variables["_import_extras"]:
            variables["_import_extra_dumps"] = _render(
                find["ansible.builtin.set_fact"]["_import_extra_dumps"],
                {**variables, "item": item})
        undeclared = _by_name(
            IMPORT, "Find dumps beside the main one that this wiki does not declare")
        variables["_import_undeclared"] = _render(
            undeclared["ansible.builtin.set_fact"]["_import_undeclared"], variables)
        return variables

    def test_declared_extra_database_is_paired_with_its_dump(self, tmp_path):
        v = self._run(tmp_path, ["main.sql", "main.main_cargo.sql"], ["main_cargo"])
        assert v["_import_extra_dumps"] == {
            "main_cargo": str(tmp_path / "main.main_cargo.sql")}
        assert v["_import_undeclared"] == []

    def test_gzipped_sibling_is_found(self, tmp_path):
        v = self._run(tmp_path, ["main.sql", "main.main_cargo.sql.gz"], ["main_cargo"])
        assert v["_import_extra_dumps"]["main_cargo"].endswith("main.main_cargo.sql.gz")

    def test_missing_dump_is_left_empty_for_the_warning(self, tmp_path):
        v = self._run(tmp_path, ["main.sql"], ["main_cargo"])
        assert v["_import_extra_dumps"] == {"main_cargo": ""}

    def test_undeclared_dump_is_reported(self, tmp_path):
        v = self._run(tmp_path, ["main.sql", "main.main_cargo.sql"], [])
        assert v["_import_extra_dumps"] == {}
        assert v["_import_undeclared"] == ["main_cargo"]
