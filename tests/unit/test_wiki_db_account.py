"""MediaWiki connects to the bundled database with its own account (#1596).

ensure_wiki_db_account.sh runs inside the db container and makes the
account match .env and the wiki database set. These tests run it against a
fake `mariadb` that records the SQL it is fed and answers the two queries
the script makes, plus structural checks on how the account is wired in.
"""

import os
import subprocess
import sys

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers  # noqa: E402

SCRIPT = os.path.join(REPO_ROOT, "roles", "orchestrator", "files",
                      "ensure_wiki_db_account.sh")
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
COMPOSE = os.path.join(REPO_ROOT, "roles", "orchestrator", "files",
                       "compose", "docker-compose.yml")

FAKE_MARIADB = r"""#!/bin/sh
for a in "$@"; do
  case "$a" in
    *"SELECT Db FROM mysql.db"*) cat "$FAKE_DIR/granted"; exit 0 ;;
    *"SHOW DATABASES"*) cat "$FAKE_DIR/databases"; exit 0 ;;
    *"SELECT 1"*) echo "$MYSQL_PWD" >> "$FAKE_DIR/logins"; exit 0 ;;
  esac
done
cat >> "$FAKE_DIR/sql"
"""


@pytest.fixture
def db(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "mariadb"
    fake.write_text(FAKE_MARIADB)
    fake.chmod(0o755)
    (tmp_path / "granted").write_text("")
    (tmp_path / "databases").write_text(
        "information_schema\nmain\nmain_cargo\nmysql\nstray\n")
    (tmp_path / "sql").write_text("")

    def run(*dbs, user="mediawiki", password="pw", granted=""):
        (tmp_path / "granted").write_text(granted)
        (tmp_path / "sql").write_text("")
        env = dict(os.environ, FAKE_DIR=str(tmp_path),
                   PATH="%s:%s" % (bindir, os.environ["PATH"]),
                   WIKI_DB_USER=user, WIKI_DB_PASSWORD=password,
                   MYSQL_ROOT_PASSWORD="rootpw")
        with open(SCRIPT) as f:
            result = subprocess.run(["sh", "-s", "--", *dbs], stdin=f,
                                    capture_output=True, text=True, env=env)
        return result, (tmp_path / "sql").read_text()
    return run


class TestTheScript:
    def test_grants_each_database_with_wildcards_escaped(self, db):
        result, sql = db("main", "main_cargo")
        assert result.returncode == 0, result.stderr
        assert "GRANT ALL PRIVILEGES ON `main`.* TO 'mediawiki'@'%';" in sql
        assert "GRANT ALL PRIVILEGES ON `main\\_cargo`.* TO 'mediawiki'@'%';" in sql
        assert "*.*" not in sql.replace("ON `", "")

    def test_password_is_escaped_for_sql_and_stays_off_argv(self, db):
        result, sql = db("main", password="it's\\x")
        assert result.returncode == 0, result.stderr
        assert "IDENTIFIED BY 'it''s\\\\x';" in sql

    def test_grants_on_databases_that_left_the_set_are_revoked(self, db):
        result, sql = db("main", granted="main\nmain\\_cargo\n")
        assert "REVOKE ALL PRIVILEGES ON `main\\_cargo`.* FROM 'mediawiki'@'%';" in sql
        assert "REVOKE ALL PRIVILEGES ON `main`" not in sql

    def test_reports_databases_the_account_cannot_use(self, db):
        result, _ = db("main", "main_cargo")
        lines = result.stdout.splitlines()
        assert "UNDECLARED stray" in lines
        assert not any("mysql" in l or "information_schema" in l for l in lines)
        assert lines[-1] == "ENSURED mediawiki"

    def test_root_account_is_left_alone(self, db):
        result, sql = db("main", user="root")
        assert result.stdout.strip() == "SKIPPED account is root"
        assert sql == ""

    @pytest.mark.parametrize("user", ["x';drop", "me-wiki", "a b"])
    def test_unsafe_user_names_are_refused(self, db, user):
        result, sql = db("main", user=user)
        assert result.returncode == 2
        assert sql == ""

    def test_empty_password_is_refused(self, db):
        result, sql = db("main", password="")
        assert result.returncode == 2
        assert sql == ""


class TestWiring:
    def _tasks(self, name):
        with open(os.path.join(TASKS, name)) as f:
            return yaml.safe_load(f)

    def _compose_start(self):
        play = next(t for t in self._tasks("start.yml")
                    if "Docker Compose" in str(t.get("name", "")))
        return play["block"]

    def test_start_ensures_before_web_starts(self):
        # web runs update.php for every wiki as soon as it boots, so the
        # account must exist before the full `up` starts it.
        tasks = self._compose_start()
        names = [t.get("name") for t in tasks]
        db_first = names.index("Start the database before MediaWiki")
        ensure = names.index(
            "Ensure MediaWiki's database account before web starts")
        up = names.index("Start containers")
        assert db_first < ensure < up
        assert tasks[db_first]["ansible.builtin.command"]["cmd"].endswith(
            "up -d db")

    def test_start_still_ensures_when_nothing_was_started(self):
        tasks = self._compose_start()
        names = [t.get("name") for t in tasks]
        late = tasks[names.index("Ensure MediaWiki's database account")]
        assert (names.index("Wait for web container to report healthy")
                < names.index("Ensure MediaWiki's database account"))
        assert "_start_account_ensured" in str(late["when"])

    def test_bundled_db_is_pinged_over_tcp(self):
        ping = next(
            t for t in self._tasks("ensure_wiki_db_account.yml")[-1]["block"]
            if t.get("name") == "Wait for the database to accept root logins")
        assert "--protocol=tcp -h 127.0.0.1" in (
            ping["ansible.builtin.command"]["cmd"])

    def test_skipped_for_external_db_root_and_kubernetes(self):
        decide = next(t for t in self._tasks("ensure_wiki_db_account.yml")
                      if t.get("name") == "Decide whether the account needs ensuring")
        expr = decide["ansible.builtin.set_fact"]["_ensure_needed"]
        for env, orch, needed in (
                ({"WIKI_DB_USER": "mediawiki"}, "compose", True),
                ({"WIKI_DB_USER": "root"}, "compose", False),
                ({}, "compose", False),
                ({"WIKI_DB_USER": "mediawiki", "USE_EXTERNAL_DB": "true"}, "compose", False),
                ({"WIKI_DB_USER": "mediawiki"}, "kubernetes", False)):
            templar = Templar(loader=DataLoader(), variables={
                "_ensure_env": {"variables": env},
                "instance_orchestrator": orch})
            assert templar.template(trust_as_template(expr)) is needed, (env, orch)

    def test_web_gets_the_wiki_account_and_db_can_set_it(self):
        with open(COMPOSE) as f:
            services = yaml.safe_load(f)["services"]
        assert "MYSQL_USER=${WIKI_DB_USER}" in services["web"]["environment"]
        assert "MYSQL_PASSWORD=${WIKI_DB_PASSWORD}" in services["web"]["environment"]
        assert not any("MYSQL_ROOT_PASSWORD" in e or "${MYSQL_PASSWORD}" in e
                       for e in services["web"]["environment"])
        assert "WIKI_DB_USER=${WIKI_DB_USER}" in services["db"]["environment"]
        assert "WIKI_DB_PASSWORD=${WIKI_DB_PASSWORD}" in services["db"]["environment"]

    def test_create_defaults_to_the_mediawiki_account_on_compose(self):
        tasks = yaml.safe_load(open(os.path.join(
            REPO_ROOT, "roles", "create", "tasks", "_passwords.yml")))
        choose = next(t for t in tasks if t.get("name") == "Choose MediaWiki's database account")
        expr = choose["ansible.builtin.set_fact"]["_wiki_db_user"]
        for given, orch, expected in (("", "compose", "mediawiki"),
                                      ("", "kubernetes", "root"),
                                      ("root", "compose", "root"),
                                      ("wiki", "compose", "wiki")):
            templar = Templar(loader=DataLoader(), variables={
                "wikidbuser": given, "orchestrator": orch})
            assert templar.template(trust_as_template(expr)) == expected


class TestFastPath:
    @pytest.mark.parametrize("env, uses", [
        ({"WIKI_DB_USER": "mediawiki"}, True),
        ({"WIKI_DB_USER": "root"}, False),
        ({}, False),
        ({"WIKI_DB_USER": "op", "USE_EXTERNAL_DB": "true"}, False),
    ])
    def test_start_hands_an_own_account_to_ansible(self, env, uses):
        assert _helpers._uses_wiki_db_account(env) is uses
