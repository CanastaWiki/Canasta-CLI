"""A missing MYSQL_PASSWORD is only safe to replace when nothing uses it.

docker-compose.yml hands the value to two services:

    db  -> MYSQL_ROOT_PASSWORD=${MYSQL_PASSWORD}
    web -> MYSQL_PASSWORD=${MYSQL_PASSWORD}

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

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
HEAL = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "heal_mysql_password.yml")
START = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose",
    "docker-compose.yml")


def _tasks():
    with open(HEAL) as f:
        return yaml.safe_load(f)


def _named(name):
    for t in _tasks():
        if t.get("name") == name:
            return t
        for inner in t.get("block") or []:
            if inner.get("name") == name:
                return inner
    raise AssertionError("task not found: %s" % name)


class TestItRefusesWhenADatabaseExists:
    def test_an_existing_volume_is_fatal(self):
        task = _named("Fail when a database already exists")
        assert "ansible.builtin.fail" in task
        assert "_heal_db_volume.rc == 0" in str(task["when"]), (
            "the refusal must key on the volume being found"
        )

    def test_the_message_says_why_generating_would_not_help(self):
        msg = _named("Fail when a database already exists")[
            "ansible.builtin.fail"]["msg"]
        assert "only applies the root password when it first initialises" in msg

    def test_the_message_names_a_way_back(self):
        # The operator cannot recover the password by thinking harder; it
        # exists only in a backup or another copy of .env.
        msg = _named("Fail when a database already exists")[
            "ansible.builtin.fail"]["msg"]
        assert "canasta backup restore" in msg

    def test_the_refusal_precedes_the_generation(self):
        block = next(t for t in _tasks()
                     if t.get("name") == "Heal the missing database password")
        names = [x.get("name") for x in block["block"]]
        assert (names.index("Fail when a database already exists")
                < names.index("Generate a database password"))


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
        assert "_heal_mysql_pw" in str(block["when"])


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
        assert "MYSQL_PASSWORD=${MYSQL_PASSWORD}" in text
