"""create -d and add -d bring in a wiki's extra databases.

`canasta export` writes each extra database a wiki declares beside the main
dump as <stem>.<database>.sql (or .sql.gz). add and create import every such
sibling whose database name is usable and declare it for the wiki in
wikis.yaml; other siblings are reported and skipped.

The real add.yml / _database.yml tasks, import_extra_databases.yml and
import_database.yml run against a stubbed db_admin.yml, exec.yml and
copy_into_container.yml that log what they would do.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_databases import canasta_extra_dump_plan  # noqa: E402

COMMON = os.path.join(REPO_ROOT, "roles", "common")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
CREATE_DB = os.path.join(REPO_ROOT, "roles", "create", "tasks", "_database.yml")


class TestPlan:
    GROUPS = [{"wiki": "main", "databases": ["main", "main_cargo"]}]

    def _plan(self, names, stem="/d/docs", wiki="docs", groups=None):
        return canasta_extra_dump_plan(
            [stem + "." + n for n in names], stem, wiki,
            self.GROUPS if groups is None else groups)

    def test_plain_and_gzipped_siblings(self):
        plan = self._plan(["docs_cargo.sql", "other-db.sql.gz"])
        assert plan == {"load": ["docs_cargo", "other-db"], "skipped": []}

    def test_both_forms_of_one_database_load_it_once(self):
        plan = self._plan(["docs_cargo.sql", "docs_cargo.sql.gz"])
        assert plan["load"] == ["docs_cargo"]

    def test_stem_with_dots(self):
        plan = self._plan(["docs_cargo.sql.gz"], stem="/d/docs.2026-10-01")
        assert plan == {"load": ["docs_cargo"], "skipped": []}

    def test_invalid_name_is_skipped(self):
        plan = self._plan(["bad.name.sql", "bad$name.sql"])
        assert plan["load"] == []
        assert [s["file"] for s in plan["skipped"]] == [
            "/d/docs.bad$name.sql", "/d/docs.bad.name.sql"]
        assert all("is invalid" in s["reason"] for s in plan["skipped"])

    def test_own_system_and_other_wikis_databases_are_skipped(self):
        plan = self._plan(["docs.sql", "mysql.sql", "main.sql",
                           "main_cargo.sql.gz"])
        assert plan["load"] == []
        reasons = {s["file"]: s["reason"] for s in plan["skipped"]}
        assert "wiki's own database" in reasons["/d/docs.docs.sql"]
        assert "system database" in reasons["/d/docs.mysql.sql"]
        assert "belongs to wiki 'main'" in reasons["/d/docs.main.sql"]
        assert "belongs to wiki 'main'" in reasons["/d/docs.main_cargo.sql.gz"]

    def test_database_the_wiki_already_declares_is_loaded(self):
        groups = [{"wiki": "docs", "databases": ["docs", "docs_cargo"]}]
        plan = self._plan(["docs_cargo.sql"], groups=groups)
        assert plan["load"] == ["docs_cargo"]


def _stub_root(tmp_path):
    """A canasta_root whose db_admin, exec and container copy only log."""
    log = tmp_path / "log"
    tasks = tmp_path / "root" / "roles" / "orchestrator" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "db_admin.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.set_fact": {
            "_db_admin_service": "db",
            "_db_admin_pwd": "PWD",
            "_db_admin_args": "-u root",
        }}]))
    (tasks / "exec.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.shell": "printf 'EXEC %s\\n' \"$CMD\" >> " + str(log),
        "environment": {"CMD": "{{ exec_command }}"},
    }]))
    (tasks / "copy_into_container.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.shell":
            "printf 'COPY %s %s\\n' \"$DEST\" \"$(head -1 \"$SRC\")\" >> "
            + str(log),
        "environment": {"DEST": "{{ copy_dest }}", "SRC": "{{ copy_src }}"},
    }]))
    return str(tmp_path / "root"), log


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _tasks(path, names):
    with open(path) as f:
        found = {t.get("name"): t for t in _walk(yaml.safe_load(f))}
    return [found[n] for n in names]


def _instance(tmp_path, wikis):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(
        yaml.safe_dump({"wikis": wikis}, sort_keys=False))
    return inst


def _dumps(tmp_path, names):
    d = tmp_path / "dumps"
    d.mkdir()
    for n in names:
        (d / n).write_text("-- dump %s\n" % n)
    return d


def _run(tmp_path, play_vars, tasks):
    root, log = _stub_root(tmp_path)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": root,
                 "ansible_python_interpreter": sys.executable, **play_vars},
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="canasta_minimal",
               ANSIBLE_CALLBACK_PLUGINS=os.path.join(REPO_ROOT, "callback_plugins"),
               ANSIBLE_ROLES_PATH=os.path.join(REPO_ROOT, "roles"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    lines = log.read_text().splitlines() if log.exists() else []
    return proc, lines


def _wikis(inst):
    return yaml.safe_load((inst / "config" / "wikis.yaml").read_text())["wikis"]


def _loaded(lines):
    """[(database, dump first line)] in load order."""
    out = []
    copied = None
    for line in lines:
        if line.startswith("COPY "):
            copied = line.split(" ", 2)[2]
        elif line.startswith("EXEC ") and "mariadb -u root '" in line:
            out.append((line.split("mariadb -u root '")[1].split("'")[0], copied))
    return out


class TestAdd:
    NAMES = ("Import database dump", "Read the instance's databases",
             "Import the extra databases beside the dump",
             "Record the extra databases imported", "Add wiki to wikis.yaml",
             "Declare the imported extra databases for the wiki",
             "Report wiki added")
    MAIN = [{"id": "main", "url": "example.com", "name": "Main",
             "extra_databases": ["main_cargo"]}]

    def _add(self, tmp_path, files, main_dump="docs.sql"):
        inst = _instance(tmp_path, self.MAIN)
        d = _dumps(tmp_path, files)
        proc, lines = _run(tmp_path, {
            "instance_path": str(inst), "instance_id": "inst",
            "wiki": "docs", "database": str(d / main_dump),
            "_add_domain": "example.com", "_add_path": "docs",
        }, _tasks(ADD, self.NAMES))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        docs = next(w for w in _wikis(inst) if w["id"] == "docs")
        return proc, lines, docs

    def test_discovers_imports_and_declares_siblings(self, tmp_path):
        proc, lines, docs = self._add(tmp_path, [
            "docs.sql", "docs.docs_cargo.sql", "docs.smw-store.sql.gz"])
        assert _loaded(lines) == [
            ("docs", "-- dump docs.sql"),
            ("docs_cargo", "-- dump docs.docs_cargo.sql"),
            ("smw-store", "-- dump docs.smw-store.sql.gz"),
        ]
        assert any("gunzip -c /tmp/import.sql.gz" in ln and "'smw-store'" in ln
                   for ln in lines)
        assert any("CREATE DATABASE IF NOT EXISTS `docs_cargo`" in ln
                   for ln in lines)
        assert any("CREATE DATABASE IF NOT EXISTS `smw-store`" in ln
                   for ln in lines)
        assert docs["extra_databases"] == ["docs_cargo", "smw-store"]
        assert ("with extra database(s) docs_cargo, smw-store imported and "
                "declared") in proc.stdout

    def test_gzipped_sibling_preferred_like_import(self, tmp_path):
        _, lines, docs = self._add(tmp_path, [
            "docs.sql", "docs.docs_cargo.sql", "docs.docs_cargo.sql.gz"])
        assert any("gunzip" in ln and "'docs_cargo'" in ln for ln in lines)
        assert docs["extra_databases"] == ["docs_cargo"]

    def test_stem_with_dots(self, tmp_path):
        _, lines, docs = self._add(
            tmp_path, ["docs.2026.sql", "docs.2026.docs_cargo.sql"],
            main_dump="docs.2026.sql")
        assert [db for db, _ in _loaded(lines)] == ["docs", "docs_cargo"]
        assert docs["extra_databases"] == ["docs_cargo"]

    def test_unusable_siblings_are_reported_and_skipped(self, tmp_path):
        proc, lines, docs = self._add(tmp_path, [
            "docs.sql", "docs.bad.name.sql", "docs.main_cargo.sql",
            "docs.mysql.sql"])
        assert [db for db, _ in _loaded(lines)] == ["docs"]
        assert not any("CREATE DATABASE IF NOT EXISTS `main_cargo`" in ln
                       for ln in lines)
        assert "extra_databases" not in docs
        assert "docs.bad.name.sql: extra database name 'bad.name' is invalid" \
            in proc.stdout
        assert "database 'main_cargo' belongs to wiki 'main'" in proc.stdout
        assert "'mysql' is a MariaDB system database" in proc.stdout
        assert "with extra database" not in proc.stdout

    def test_nothing_extra_without_siblings(self, tmp_path):
        proc, lines, docs = self._add(tmp_path, ["docs.sql", "other.x.sql"])
        assert [db for db, _ in _loaded(lines)] == ["docs"]
        assert "extra_databases" not in docs
        assert "WARNING" not in proc.stdout
        assert "Wiki 'docs' added to instance 'inst'." in proc.stdout

    def test_declared_before_the_restart_and_gitops_capture(self):
        with open(ADD) as f:
            names = [t.get("name") for t in yaml.safe_load(f)]
        declare = names.index("Declare the imported extra databases for the wiki")
        assert names.index("Add wiki to wikis.yaml") < declare
        assert declare < names.index("Restart instance")
        assert declare < names.index("Capture the new wiki for gitops")


class TestCreate:
    NAMES = ("Import database dump", "Read the instance's databases",
             "Import the extra databases beside the dump",
             "Declare the imported extra databases for the wiki",
             "Report the extra databases imported")

    def _create(self, tmp_path, files, wikis=None):
        inst = _instance(tmp_path, wikis or [
            {"id": "main", "url": "example.com", "name": "Main"}])
        d = _dumps(tmp_path, files)
        proc, lines = _run(tmp_path, {
            "instance_path": str(inst), "wiki": "main",
            "database": str(d / "main.sql"),
        }, _tasks(CREATE_DB, self.NAMES))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc, lines, _wikis(inst)

    def test_imports_and_declares_siblings(self, tmp_path):
        proc, lines, wikis = self._create(
            tmp_path, ["main.sql", "main.main_cargo.sql"])
        assert [db for db, _ in _loaded(lines)] == ["main", "main_cargo"]
        assert wikis[0]["extra_databases"] == ["main_cargo"]
        assert "Imported and declared extra database(s) main_cargo for wiki " \
            "'main'." in proc.stdout

    def test_wiki_missing_from_a_given_wikis_yaml_skips_discovery(self, tmp_path):
        proc, lines, wikis = self._create(
            tmp_path, ["main.sql", "main.main_cargo.sql"],
            wikis=[{"id": "other", "url": "example.com", "name": "Other"}])
        assert [db for db, _ in _loaded(lines)] == ["main"]
        assert "extra_databases" not in wikis[0]
        assert "Imported and declared" not in proc.stdout


class TestImportUnchanged:
    """import still loads only declared databases and reports the rest."""

    IMPORT = os.path.join(REPO_ROOT, "playbooks", "import.yml")
    NAMES = ("Read the wiki's extra databases",
             "Import the wiki's extra databases")

    def test_declared_only_with_warnings(self, tmp_path):
        inst = _instance(tmp_path, [
            {"id": "main", "url": "example.com", "name": "Main",
             "extra_databases": ["main_cargo", "main_smw"]}])
        d = _dumps(tmp_path, ["main.sql", "main.main_cargo.sql.gz",
                              "main.stray.sql"])
        proc, lines = _run(tmp_path, {
            "instance_path": str(inst), "wiki": "main",
            "database": str(d / "main.sql"),
        }, _tasks(self.IMPORT, self.NAMES))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert [db for db, _ in _loaded(lines)] == ["main_cargo"]
        assert "no dump found for extra database(s) main_smw of wiki " \
            "'main'" in proc.stdout
        assert "WARNING: not imported: stray, which wiki 'main' does not " \
            "declare" in proc.stdout
        assert _wikis(inst)[0]["extra_databases"] == ["main_cargo", "main_smw"]
