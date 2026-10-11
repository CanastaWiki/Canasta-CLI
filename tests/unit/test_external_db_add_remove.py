"""add, remove and import reach the database where db_admin.yml says.

On an external database there is no db service, so installing, importing
and dropping a wiki's databases go through the web container as the
operator's account. remove drops the extra databases the wiki declares
(not ones another wiki shares) and says so when it cannot drop them.
Kubernetes create refuses a MediaWiki account other than root.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
REMOVE = os.path.join(REPO_ROOT, "playbooks", "remove.yml")
IMPORT_DB = os.path.join(REPO_ROOT, "roles", "mediawiki", "tasks",
                         "import_database.yml")
VALIDATE = os.path.join(REPO_ROOT, "roles", "create", "tasks",
                        "_validate_inputs.yml")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _named(tasks, name):
    return next(t for t in _walk(tasks) if t.get("name") == name)


def _run_play(tmp_path, play_vars, tasks):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": play_vars,
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path)


class TestAdd:
    def test_install_uses_the_env_database_server(self):
        install = _named(_load(ADD), "Install new wiki")
        args = install["vars"]["install_php_args"]
        assert "--dbserver=db" not in args
        assert ("--dbserver={{ _add_db_env.variables.MYSQL_HOST | default('db') }}"
                ":{{ _add_db_env.variables.MYSQL_PORT | default('3306') }}") in args

    def test_nothing_runs_in_the_db_service(self):
        for path in (ADD, IMPORT_DB):
            for t in _walk(_load(path)):
                assert (t.get("vars") or {}).get("exec_service") != "db", path
                assert (t.get("vars") or {}).get("copy_service") != "db", path
            with open(path) as f:
                assert "MYSQL_ROOT_PASSWORD" not in f.read(), path

    def test_import_runs_where_db_admin_says(self):
        tasks = _load(IMPORT_DB)
        assert tasks[0]["ansible.builtin.include_tasks"].endswith(
            "/roles/orchestrator/tasks/db_admin.yml")
        for t in _walk(tasks):
            v = t.get("vars") or {}
            if "exec_service" in v:
                assert v["exec_service"] == "{{ _db_admin_service }}"
                if "mariadb" in v["exec_command"]:
                    assert "{{ _db_admin_pwd }} mariadb {{ _db_admin_args }}" \
                        in v["exec_command"]
            if "copy_service" in v:
                assert v["copy_service"] == "{{ _db_admin_service }}"


FAKE_MARIADB = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_DIR/args"
echo "$MYSQL_PWD" > "$FAKE_DIR/pwd"
if [ -n "$FAKE_FAIL" ]; then echo "$FAKE_FAIL" >&2; exit 1; fi
"""


def _stub_root(tmp_path):
    """A canasta_root whose db_admin.yml and exec.yml run locally."""
    tasks = tmp_path / "root" / "roles" / "orchestrator" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "db_admin.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.set_fact": {
            "_db_admin_service": "web",
            "_db_admin_pwd": 'MYSQL_PWD="$MYSQL_PASSWORD"',
            "_db_admin_args": '-u "${MYSQL_USER:-root}"',
        }}]))
    # Quote and wrap the command the way exec.yml does.
    (tasks / "exec.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.shell": {
            "cmd": "/bin/sh -c {{ exec_command | quote }}"},
        "register": "exec_result",
        "environment": {
            "FAKE_DIR": str(tmp_path),
            "PATH": "%s:%s" % (tmp_path / "bin", os.environ["PATH"]),
            "MYSQL_PASSWORD": "opw",
            "MYSQL_USER": "operator",
            "FAKE_FAIL": "{{ fake_fail | default('') }}",
        },
    }]))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "mariadb"
    fake.write_text(FAKE_MARIADB)
    fake.chmod(0o755)
    return str(tmp_path / "root")


def _run_remove(tmp_path, db_groups, wiki="docs", fake_fail=""):
    remove = _load(REMOVE)
    names = ("Choose the databases to drop", "Drop the wiki's databases",
             "Report wiki removed")
    tasks = [t for t in remove if t.get("name") in names]
    assert len(tasks) == len(names)
    out = tmp_path / "out.json"
    tasks.append({"ansible.builtin.copy": {
        "content": "{{ {'drop': _remove_drop_dbs, 'left': _remove_dbs_left} | to_json }}",
        "dest": str(out)}})
    proc = _run_play(tmp_path, {
        "canasta_root": _stub_root(tmp_path),
        "wiki": wiki,
        "instance_id": "inst",
        "fake_fail": fake_fail,
        "_remove_wikis": {"db_groups": db_groups},
    }, tasks)
    data = json.loads(out.read_text()) if out.exists() else None
    args_file = tmp_path / "args"
    args = args_file.read_text().splitlines() if args_file.exists() else None
    return proc, data, args


class TestRemove:
    GROUPS = [
        {"wiki": "main", "databases": ["main", "shared_cargo"]},
        {"wiki": "docs", "databases": ["docs", "docs-cargo", "shared_cargo",
                                       "mysql"]},
    ]

    def test_drops_the_wiki_and_its_own_extra_databases(self, tmp_path):
        proc, data, args = _run_remove(tmp_path, self.GROUPS)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert data == {"drop": ["docs", "docs-cargo"], "left": []}
        assert args == [
            "-u", "operator", "-e",
            "DROP DATABASE IF EXISTS `docs`; DROP DATABASE IF EXISTS `docs-cargo`; "]
        assert (tmp_path / "pwd").read_text().strip() == "opw"
        assert "removed from instance 'inst'." in proc.stdout
        assert "left in place" not in proc.stdout

    def test_failed_drop_warns_and_names_the_databases(self, tmp_path):
        proc, data, _ = _run_remove(tmp_path, self.GROUPS,
                                    fake_fail="Access denied for DROP")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert data["left"] == ["docs", "docs-cargo"]
        assert "WARNING: could not drop database(s) docs, docs-cargo" in proc.stdout
        assert "Access denied for DROP" in proc.stdout
        assert "its database(s) docs, docs-cargo were left in place" in proc.stdout

    def test_wiki_database_another_wiki_declares_is_kept(self, tmp_path):
        groups = [{"wiki": "main", "databases": ["main", "docs"]},
                  {"wiki": "docs", "databases": ["docs"]}]
        proc, data, args = _run_remove(tmp_path, groups)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert data == {"drop": [], "left": []}
        assert args is None

    def test_no_ignore_errors_on_the_drop(self):
        block = _named(_load(REMOVE), "Drop the wiki's databases")
        assert "rescue" in block
        for t in _walk(block["block"]):
            assert "ignore_errors" not in t
            assert "apply" not in t


class TestKubernetesWikiDbUser:
    def _run(self, tmp_path, orchestrator, wikidbuser):
        task = _named(_load(VALIDATE),
                      "Fail if --wikidbuser names an account other than root on Kubernetes")
        play_vars = {"orchestrator": orchestrator}
        if wikidbuser is not None:
            play_vars["wikidbuser"] = wikidbuser
        return _run_play(tmp_path, play_vars, [task])

    def test_kubernetes_refuses_a_non_root_account(self, tmp_path):
        proc = self._run(tmp_path, "kubernetes", "wiki")
        assert proc.returncode != 0
        assert "--wikidbuser wiki is not supported on Kubernetes" in \
            proc.stdout + proc.stderr

    def test_kubernetes_accepts_root_or_no_account(self, tmp_path):
        for given in (None, "", "root"):
            proc = self._run(tmp_path, "kubernetes", given)
            assert proc.returncode == 0, (given, proc.stdout + proc.stderr)

    def test_compose_accepts_any_account(self, tmp_path):
        proc = self._run(tmp_path, "compose", "wiki")
        assert proc.returncode == 0, proc.stdout + proc.stderr
