"""Warn about wiki databases the database server does not have.

A gitops pull can bring in a wiki added on another host; gitops carries no
databases, so the wiki arrives with none. After the pull (and on reconcile)
each declared database missing from SHOW DATABASES gets a warning naming
the wiki and how to supply it. An unreachable server skips the check.
"""

import os
import re
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
WARN = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                    "warn_missing_wiki_databases.yml")
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")
RECONCILE = os.path.join(REPO_ROOT, "roles", "instance_lifecycle", "tasks",
                         "reconcile.yml")

FAKE_MARIADB = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_DIR/args"
echo "$MYSQL_PWD" > "$FAKE_DIR/pwd"
if [ -n "$FAKE_FAIL" ]; then echo "ERROR 2002: Can't connect" >&2; exit 1; fi
printf '%s\\n' $FAKE_DBS
"""

WIKIS = {"wikis": [
    {"id": "main", "url": "example.com", "name": "Main"},
    {"id": "docs", "url": "example.com/docs", "name": "Docs",
     "extra_databases": ["docs_cargo"]},
]}


def _run(tmp_path, dbs, fail=False):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump(WIKIS))

    stub = tmp_path / "root" / "roles" / "orchestrator" / "tasks"
    stub.mkdir(parents=True)
    (stub / "db_admin.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.set_fact": {
            "_db_admin_exec": "",
            "_db_admin_pwd": 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"',
            "_db_admin_args": "-u root",
        }}]))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "mariadb"
    fake.write_text(FAKE_MARIADB)
    fake.chmod(0o755)

    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": str(tmp_path / "root"),
                 "instance_path": str(inst),
                 "instance_id": "inst",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": WARN}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               PATH="%s:%s" % (bindir, os.environ["PATH"]),
               FAKE_DIR=str(tmp_path), FAKE_DBS=" ".join(dbs),
               FAKE_FAIL="1" if fail else "", MYSQL_ROOT_PASSWORD="rootpw")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    args_file = tmp_path / "args"
    args = args_file.read_text().splitlines() if args_file.exists() else None
    return proc, args


def _warnings(stdout):
    return [line for line in stdout.splitlines()
            if re.search(r"WARNING: (wiki|database) '", line)]


SYSTEM = ["information_schema", "mysql", "performance_schema", "sys"]


def test_no_warning_when_every_database_exists(tmp_path):
    proc, args = _run(tmp_path, SYSTEM + ["main", "docs", "docs_cargo"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert args == ["-u", "root", "-N", "-e", "SHOW DATABASES"]
    assert (tmp_path / "pwd").read_text().strip() == "rootpw"
    assert _warnings(proc.stdout) == []
    assert "was not checked" not in proc.stdout


def test_missing_wiki_database_names_the_wiki_and_next_steps(tmp_path):
    proc, _ = _run(tmp_path, SYSTEM + ["main"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    warnings = _warnings(proc.stdout)
    assert len(warnings) == 2, proc.stdout
    wiki_warning = next(w for w in warnings if "wiki 'docs' has no database" in w)
    assert "canasta import -i inst -w docs -d <dump>" in wiki_warning
    assert "canasta backup restore -i inst -s <snapshot> -w docs" in wiki_warning
    assert "also overwrites" in wiki_warning
    extra = next(w for w in warnings if "database 'docs_cargo'" in w)
    assert "which wiki 'docs' declares" in extra
    assert "<stem>.docs_cargo.sql" in extra
    assert "canasta import -i inst -w docs -d <dump>" in extra
    assert not any("'main'" in w for w in warnings)


def test_missing_extra_database_alone(tmp_path):
    proc, _ = _run(tmp_path, SYSTEM + ["main", "docs"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    warnings = _warnings(proc.stdout)
    assert len(warnings) == 1, proc.stdout
    assert "database 'docs_cargo', which wiki 'docs' declares" in warnings[0]


def test_unreachable_server_skips_the_check(tmp_path):
    proc, _ = _run(tmp_path, [], fail=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "was not checked" in proc.stdout
    assert _warnings(proc.stdout) == []


def _includes(path):
    with open(path) as f:
        tasks = yaml.safe_load(f)
    return [t for t in tasks
            if str(t.get("ansible.builtin.include_tasks", "")).strip().endswith(
                "/roles/orchestrator/tasks/warn_missing_wiki_databases.yml")]


def test_pull_runs_the_check_after_rendering():
    with open(PULL) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    [check] = _includes(PULL)
    assert names.index(check["name"]) > names.index("Render wikis.yaml from template")
    assert "when" not in check


def test_reconcile_runs_the_check_on_compose_after_converging():
    with open(RECONCILE) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    [check] = _includes(RECONCILE)
    assert names.index(check["name"]) > names.index("Converge containers (sync profiles + up -d)")
    assert check["when"] == "instance_orchestrator | default('compose') == 'compose'"
