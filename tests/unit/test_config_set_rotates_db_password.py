"""`config set MYSQL_PASSWORD` rotates the bundled Compose database password.

The bundled MariaDB reads MYSQL_ROOT_PASSWORD only when it initializes an
empty data directory, so writing .env alone locked the wiki out. The set now
changes the password in the running server first (SQL on stdin, the
container's current password authenticating), and only then writes .env.
External-database instances are refused without --force.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "config", "tasks")
SET_SINGLE = os.path.join(TASKS, "_set_single.yml")
ROTATE = os.path.join(TASKS, "_rotate_db_password.yml")
DEFAULTS = os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml")

REFUSE = "Refuse MYSQL_PASSWORD for an external database without --force"
ROTATE_INCLUDE = "Rotate the bundled database password"


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _by_name(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
        for nested in ("block", "rescue", "always"):
            found = _by_name(task.get(nested, []), name)
            if found:
                return found
    return None


def _when(task, **variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return all(templar.template(trust_as_template("{{ %s }}" % cond))
               for cond in task["when"])


def _vars(key="MYSQL_PASSWORD", orchestrator="compose", force=False,
          ext_db=""):
    return dict(_config_key=key, instance_orchestrator=orchestrator,
                force=force, _set_ext_db={"value": ext_db})


def test_rotation_is_the_last_step_before_the_env_write():
    names = [t.get("name") for t in _load(SET_SINGLE)]
    assert names.index(REFUSE) < names.index("Validate key")
    assert names.index("Apply side effects") < names.index(ROTATE_INCLUDE)
    assert names.index(ROTATE_INCLUDE) + 1 == names.index("Set value in .env")


@pytest.mark.parametrize("kwargs, refused, rotated", [
    ({}, False, True),
    ({"ext_db": "false"}, False, True),
    ({"ext_db": "true"}, True, False),
    ({"ext_db": "True", "force": True}, False, False),
    ({"orchestrator": "kubernetes"}, False, False),
    ({"key": "MW_SITE_SERVER"}, False, False),
])
def test_refusal_and_rotation_conditions(kwargs, refused, rotated):
    tasks = _load(SET_SINGLE)
    assert _when(_by_name(tasks, REFUSE), **_vars(**kwargs)) is refused
    assert _when(_by_name(tasks, ROTATE_INCLUDE), **_vars(**kwargs)) is rotated


def _sql(password):
    task = _by_name(_load(ROTATE), "Write the password change")
    variables = {"_config_value": password}
    variables.update({k: trust_as_template(v) for k, v in task["vars"].items()})
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(
        trust_as_template(task["ansible.builtin.copy"]["content"]))


def test_sql_changes_both_root_accounts():
    assert _sql("s3cret") == (
        "SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES');\n"
        "ALTER USER 'root'@'%' IDENTIFIED BY 's3cret';\n"
        "ALTER USER IF EXISTS 'root'@'localhost' IDENTIFIED BY 's3cret';\n")


# Backslashes stay literal under NO_BACKSLASH_ESCAPES; only quotes double.
@pytest.mark.parametrize("password, literal", [
    ("it's", "it''s"),
    ("back\\slash", "back\\slash"),
    ("a\\'b#$", "a\\''b#$"),
])
def test_sql_escapes_quotes_and_backslashes(password, literal):
    assert "IDENTIFIED BY '%s';" % literal in _sql(password)


def test_password_write_is_not_logged_and_the_file_is_always_removed():
    tasks = _load(ROTATE)
    assert _by_name(tasks, "Write the password change")["no_log"] is True
    block = next(t for t in tasks
                 if t.get("name") == "Change the password in the bundled database")
    remove = block["always"][0]["ansible.builtin.file"]
    assert remove == {"path": "{{ _rdp_sql.path }}", "state": "absent"}
    assert block["rescue"], "a failed change must stop before .env is written"


def test_exec_passes_the_sql_on_stdin_without_logging():
    apply = _by_name(_load(ROTATE), "Apply the password change")
    exec_vars = apply["vars"]
    assert exec_vars["exec_service"] == "db"
    assert exec_vars["exec_no_log"] is True
    assert exec_vars["exec_stdin"] == "{{ _rdp_sql.path }}"
    assert "_config_value" not in exec_vars["exec_command"]


def test_no_restart_is_refused():
    task = _by_name(_load(ROTATE), "Require a restart for MYSQL_PASSWORD")
    assert task["when"] == "no_restart | default(false) | bool"


def test_mysql_password_is_a_known_key():
    names = [k["name"] for k in _load(DEFAULTS)["canasta_known_keys"]]
    assert "MYSQL_PASSWORD" in names
