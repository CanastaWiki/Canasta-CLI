"""Database administration on an external database logs in as the .env
administrator, whose credentials reach each command without the web
container holding them.

The real db_admin.yml and exec.yml run here against an instance directory
and a fake compose command that records its arguments and the
CANASTA_DB_ADMIN_* values it was given, so the tests check both what the
command line carries and what reaches the container.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")

ADMIN_PW = "adm'in$Pw=1 2"

FAKE_COMPOSE = r"""#!/bin/sh
{
  printf 'ARGV:'; printf ' [%s]' "$@"; echo
  echo "USER=${CANASTA_DB_ADMIN_USER-<unset>}"
  echo "PASSWORD=${CANASTA_DB_ADMIN_PASSWORD-<unset>}"
} >> "$FAKE_LOG"
"""


def _instance(tmp_path, env_lines):
    inst = tmp_path / "inst"
    (inst / "config" / "settings" / "wikis" / "main").mkdir(parents=True)
    (inst / ".env").write_text("".join(line + "\n" for line in env_lines))
    fake = tmp_path / "fake-compose"
    fake.write_text(FAKE_COMPOSE)
    fake.chmod(0o755)
    return inst, fake


def _play(tmp_path, inst, fake, tasks, k8s=False):
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance_path": str(inst),
            "instance_id": "site",
            "canasta_root": REPO_ROOT,
            "compose_command": str(fake),
            "instance_is_k8s": k8s,
            "ansible_python_interpreter": sys.executable,
        },
        "tasks": tasks + [{"ansible.builtin.copy": {
            "content": "{{ _out | default({}) | to_json }}",
            "dest": str(out)}}],
    }]))
    log = tmp_path / "fake.log"
    log.write_text("")
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               FAKE_LOG=str(log),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", "-v", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ADMIN_PW not in proc.stdout + proc.stderr
    return json.loads(out.read_text()), log.read_text()


EXTERNAL_ENV = ["USE_EXTERNAL_DB=true", "MYSQL_HOST=db.example.com",
                "MYSQL_USER=op", "MYSQL_PASSWORD=" + ADMIN_PW,
                "WIKI_DB_USER=mediawiki_site", "WIKI_DB_PASSWORD=wikipw"]

DB_ADMIN = {"ansible.builtin.include_tasks": os.path.join(TASKS, "db_admin.yml")}
SAVE = {"ansible.builtin.set_fact": {"_out": {
    "exec": "{{ _db_admin_exec }}", "pwd": "{{ _db_admin_pwd }}",
    "args": "{{ _db_admin_args }}", "dotenv": "{{ _db_admin_dotenv }}",
    "service": "{{ _db_admin_service }}"}}}
# As the callers run it: through `command`, which splits on any whitespace,
# including the newlines the folded scalar leaves.
RUN_EXEC = {"ansible.builtin.command": {
    "cmd": "{{ _db_admin_exec }} sh -c 'true'",
    "chdir": "{{ instance_path }}"}}


class TestDbAdmin:
    def test_external_compose_passes_the_administrator_in(self, tmp_path):
        inst, fake = _instance(tmp_path, EXTERNAL_ENV)
        out, log = _play(tmp_path, inst, fake, [DB_ADMIN, SAVE, RUN_EXEC])
        assert out["service"] == "web"
        assert out["pwd"] == 'MYSQL_PWD="$CANASTA_DB_ADMIN_PASSWORD"'
        assert '-u "$CANASTA_DB_ADMIN_USER"' in out["args"]
        assert out["dotenv"] == {"CANASTA_DB_ADMIN_USER": "MYSQL_USER",
                                 "CANASTA_DB_ADMIN_PASSWORD": "MYSQL_PASSWORD"}
        assert ADMIN_PW not in out["exec"]
        argv = next(l for l in log.splitlines() if l.startswith("ARGV:"))
        assert ("[exec] [-T] [-e] [CANASTA_DB_ADMIN_USER] [-e] "
                "[CANASTA_DB_ADMIN_PASSWORD] [web]") in argv
        assert ADMIN_PW not in argv
        assert "USER=op" in log.splitlines()
        assert "PASSWORD=" + ADMIN_PW in log.splitlines()

    def test_bundled_db_is_unchanged(self, tmp_path):
        inst, fake = _instance(tmp_path, ["MYSQL_PASSWORD=rootpw"])
        out, log = _play(tmp_path, inst, fake, [DB_ADMIN, SAVE, RUN_EXEC])
        assert out["service"] == "db"
        assert out["pwd"] == 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"'
        assert out["dotenv"] == {}
        assert " ".join(out["exec"].split()) == str(fake) + " exec -T db"
        assert "USER=<unset>" in log.splitlines()

    def test_external_kubernetes_uses_the_pod_account(self, tmp_path):
        inst, fake = _instance(tmp_path, EXTERNAL_ENV)
        out, _ = _play(tmp_path, inst, fake, [DB_ADMIN, SAVE], k8s=True)
        assert out["pwd"] == 'MYSQL_PWD="$MYSQL_PASSWORD"'
        assert '-u "${MYSQL_USER:-root}"' in out["args"]
        assert out["dotenv"] == {}
        assert out["exec"].startswith("kubectl exec")


class TestExec:
    def test_exec_dotenv_reaches_the_command_off_its_command_line(self, tmp_path):
        inst, fake = _instance(tmp_path, EXTERNAL_ENV)
        _, log = _play(tmp_path, inst, fake, [
            DB_ADMIN,
            {"ansible.builtin.include_tasks": os.path.join(TASKS, "exec.yml"),
             "vars": {"exec_service": "{{ _db_admin_service }}",
                      "exec_dotenv": "{{ _db_admin_dotenv }}",
                      "exec_command": "true"}}])
        argv = next(l for l in log.splitlines() if l.startswith("ARGV:"))
        assert "[-e] [CANASTA_DB_ADMIN_PASSWORD] [web]" in argv
        assert ADMIN_PW not in argv
        assert "PASSWORD=" + ADMIN_PW in log.splitlines()

    def test_exec_without_dotenv_passes_nothing(self, tmp_path):
        inst, fake = _instance(tmp_path, EXTERNAL_ENV)
        _, log = _play(tmp_path, inst, fake, [
            {"ansible.builtin.include_tasks": os.path.join(TASKS, "exec.yml"),
             "vars": {"exec_command": "true"}}])
        argv = next(l for l in log.splitlines() if l.startswith("ARGV:"))
        assert "[-e]" not in argv
        assert "USER=<unset>" in log.splitlines()


class TestReferencedDatabases:
    def test_keeps_only_databases_a_settings_file_names(self, tmp_path):
        inst, fake = _instance(tmp_path, EXTERNAL_ENV)
        (inst / "config" / "settings" / "wikis" / "main" / "Settings.php").write_text(
            "<?php\n$wgCargoDBname = 'main_cargo';\n")
        out, _ = _play(tmp_path, inst, fake, [
            {"ansible.builtin.include_tasks":
                os.path.join(TASKS, "referenced_databases.yml"),
             "vars": {"refdb_names": ["main_cargo", "otherapp", "main"]}},
            {"ansible.builtin.set_fact": {"_out": "{{ _refdb_referenced }}"}}])
        assert out == ["main_cargo"]

    def test_no_names_gives_none(self, tmp_path):
        inst, fake = _instance(tmp_path, EXTERNAL_ENV)
        out, _ = _play(tmp_path, inst, fake, [
            {"ansible.builtin.include_tasks":
                os.path.join(TASKS, "referenced_databases.yml"),
             "vars": {"refdb_names": []}},
            {"ansible.builtin.set_fact": {"_out": "{{ _refdb_referenced }}"}}])
        assert out == []


class TestEnsureOnAnExternalDatabase:
    def test_runs_the_script_in_web_as_the_administrator(self, tmp_path):
        inst, fake = _instance(tmp_path, EXTERNAL_ENV)
        (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump(
            {"wikis": [{"id": "main", "url": "example.com", "name": "Main"}]}))
        _, log = _play(tmp_path, inst, fake, [
            {"ansible.builtin.include_tasks":
                os.path.join(TASKS, "ensure_wiki_db_account.yml")}])
        calls = [l for l in log.splitlines() if l.startswith("ARGV:")]
        script = next(c for c in calls if "exec sh -s --" in c)
        assert "[-e] [CANASTA_DB_ADMIN_PASSWORD] [web] [sh] [-c]" in script
        assert 'WIKI_DB_USER="$MYSQL_USER"' in script
        assert 'DB_ADMIN_PASSWORD="$CANASTA_DB_ADMIN_PASSWORD"' in script
        assert script.endswith("[sh] [main]")
        assert all(ADMIN_PW not in c and "wikipw" not in c for c in calls)
        assert "PASSWORD=" + ADMIN_PW in log.splitlines()

    def test_skipped_while_mediawiki_uses_the_administrator(self, tmp_path):
        inst, fake = _instance(tmp_path, [
            line.replace("WIKI_DB_USER=mediawiki_site", "WIKI_DB_USER=op")
            for line in EXTERNAL_ENV])
        (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump(
            {"wikis": [{"id": "main", "url": "example.com", "name": "Main"}]}))
        _, log = _play(tmp_path, inst, fake, [
            {"ansible.builtin.include_tasks":
                os.path.join(TASKS, "ensure_wiki_db_account.yml")}])
        assert log == ""


class TestExport:
    def _export(self, tmp_path, env_lines):
        inst, fake = _instance(tmp_path, env_lines)
        dest = tmp_path / "out.sql"
        _, log = _play(tmp_path, inst, fake, [
            {"ansible.builtin.include_tasks": os.path.join(
                REPO_ROOT, "roles", "mediawiki", "tasks", "export_database.yml"),
             "vars": {"export_db": "main", "export_dest": str(dest),
                      "export_gzip": False}}])
        return [l for l in log.splitlines() if l.startswith("ARGV:")], log

    def test_external_database_dumps_in_web_as_the_administrator(self, tmp_path):
        calls, log = self._export(tmp_path, EXTERNAL_ENV)
        dump = next(c for c in calls if "mariadb-dump" in c)
        assert "[-e] [CANASTA_DB_ADMIN_PASSWORD] [web]" in dump
        assert '-u "$CANASTA_DB_ADMIN_USER"' in dump
        assert any(c.startswith("ARGV: [cp] [web:") for c in calls), calls
        assert all(ADMIN_PW not in c for c in calls)
        assert "PASSWORD=" + ADMIN_PW in log.splitlines()

    def test_bundled_database_dumps_in_db_as_root(self, tmp_path):
        calls, _ = self._export(tmp_path, ["MYSQL_PASSWORD=rootpw"])
        dump = next(c for c in calls if "mariadb-dump" in c)
        assert "[exec] [-T] [db]" in dump
        assert 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mariadb-dump -u root' in dump
        assert any(c.startswith("ARGV: [cp] [db:") for c in calls), calls
