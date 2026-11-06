"""A missing MYSQL_PASSWORD is only safe to replace when nothing uses it.

docker-compose.yml hands the value to db, and to web while MediaWiki still
connects as root (heal_mysql_defaults.yml copies it into WIKI_DB_PASSWORD):

    db  -> MYSQL_ROOT_PASSWORD=${MYSQL_PASSWORD}
    web -> MYSQL_PASSWORD=${WIKI_DB_PASSWORD}

MariaDB applies MARIADB_ROOT_PASSWORD only when it initialises an empty
data directory. On a volume that already holds a database the stored
password wins and the variable is ignored, so a freshly generated one
reaches web but not db: the container comes up healthy and only
MediaWiki notices it cannot authenticate. That is strictly worse than
the failure it would replace, which at least says what is wrong.

The real password is unrecoverable at that point — it lived in the lost
.env and as a hash inside the volume.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
HEAL = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "heal_mysql_password.yml")
CHECK = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "check_mysql_password.yml")
START = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose",
    "docker-compose.yml")


def _tasks():
    tasks = []
    for path in (CHECK, HEAL):
        with open(path) as f:
            tasks += yaml.safe_load(f)
    return tasks


def _refusal():
    task = _named("Decide whether a missing database password can be generated")
    return " ".join([task["ansible.builtin.set_fact"]["_mysql_password_refusal"]]
                    + list(task["vars"].values()))


def _named(name):
    for t in _tasks():
        if t.get("name") == name:
            return t
        for inner in t.get("block") or []:
            if inner.get("name") == name:
                return inner
    raise AssertionError("task not found: %s" % name)


class TestItRefusesWhenADatabaseExists:
    def test_an_existing_volume_is_refused(self):
        assert "_heal_db_volume.rc | default(1) == 0" in _refusal(), (
            "the refusal must key on the volume being found"
        )

    def test_a_refusal_is_fatal(self):
        task = _named("Fail when the database password cannot be generated")
        assert task["ansible.builtin.fail"]["msg"] == "{{ _mysql_password_refusal }}"
        assert "_mysql_password_refusal" in task["when"]

    def test_the_message_says_why_generating_would_not_help(self):
        assert "only applies the root password when it first initialises" in (
            " ".join(_refusal().split()))

    def test_the_message_names_a_way_back(self):
        # The operator cannot recover the password by thinking harder; it
        # exists only in a backup or another copy of .env.
        assert "canasta backup restore" in _refusal()

    def test_the_refusal_precedes_the_generation(self):
        with open(HEAL) as f:
            names = [t.get("name") for t in yaml.safe_load(f)]
        assert (names.index("Fail when the database password cannot be generated")
                < names.index("Heal the missing database password"))


class TestItHealsWhenThereIsNothingToLoseAccessTo:
    def test_it_generates_and_writes(self):
        gen = _named("Generate a database password")
        assert gen["ansible.builtin.include_tasks"].endswith(
            "generate_password.yml")
        write = _named("Write the generated password to .env")
        env = write["canasta_env"]
        assert env["key"] == "MYSQL_PASSWORD"
        assert env["state"] == "set"

    def test_it_does_not_log_the_password(self):
        for name in ("Read MYSQL_PASSWORD from .env",
                     "Write the generated password to .env"):
            assert _named(name)["no_log"] is True

    def test_it_only_acts_when_the_value_is_absent(self):
        block = next(t for t in _tasks()
                     if t.get("name") == "Heal the missing database password")
        assert "_mysql_password_missing" in str(block["when"])
        missing = _named("Note whether the database password is missing")
        assert "_heal_mysql_pw" in str(missing["ansible.builtin.set_fact"])


class TestItRunsBeforeAnythingStarts:
    def test_start_includes_it(self):
        with open(START) as f:
            play = next(p for p in yaml.safe_load(f)
                        if "Docker Compose" in str(p.get("name", "")))
        names = [t.get("name") for t in play["block"]]
        assert "Heal a missing database password" in names

    def test_it_precedes_the_containers_coming_up(self):
        with open(START) as f:
            play = next(p for p in yaml.safe_load(f)
                        if "Docker Compose" in str(p.get("name", "")))
        names = [t.get("name") for t in play["block"]]
        assert (names.index("Heal a missing database password")
                < names.index("Start containers"))


class TestThePremiseStillHolds:
    def test_compose_still_feeds_the_value_to_both_services(self):
        # If either consumer stops using MYSQL_PASSWORD, the reasoning
        # above needs revisiting rather than the tests being deleted.
        with open(COMPOSE) as f:
            text = f.read()
        assert "MYSQL_ROOT_PASSWORD=${MYSQL_PASSWORD}" in text
        assert "MYSQL_PASSWORD=${WIKI_DB_PASSWORD}" in text
        defaults = os.path.join(os.path.dirname(HEAL), "heal_mysql_defaults.yml")
        with open(defaults) as f:
            assert "_backfill_wiki_env.variables.MYSQL_PASSWORD" in f.read()


def _run_heal(tmp_path, env_text):
    common = os.path.join(REPO_ROOT, "roles", "common")
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(env_text)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance_path": str(inst),
            "canasta_root": REPO_ROOT,
            # No volume: `false volume inspect ...` exits non-zero.
            "inspect_command": "false",
            "ansible_python_interpreter": sys.executable,
        },
        "tasks": [{"ansible.builtin.include_tasks": HEAL}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(common, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(common, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, (inst / ".env").read_text()


class TestExternalDatabase:
    def test_it_refuses_to_generate_for_an_external_db(self, tmp_path):
        proc, env = _run_heal(
            tmp_path, "MYSQL_PASSWORD=\nUSE_EXTERNAL_DB=true\n")
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0
        assert "external database" in out
        assert "will initialise" not in out
        assert "MYSQL_PASSWORD=\n" in env

    def test_a_bundled_db_still_heals(self, tmp_path):
        proc, env = _run_heal(
            tmp_path, "MYSQL_PASSWORD=\nUSE_EXTERNAL_DB=false\n")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "will initialise" in proc.stdout
        assert "MYSQL_PASSWORD=\n" not in env
