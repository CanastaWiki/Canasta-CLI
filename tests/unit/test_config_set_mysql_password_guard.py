"""`config set MYSQL_PASSWORD` on Compose with the bundled database.

The bundled MariaDB reads MYSQL_PASSWORD only when it initializes an empty
data directory, so changing .env alone locked the wiki out of its database.
Without --force the set is refused with the rotation procedure, whose last
step is the same set with --force.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SET_SINGLE = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_set_single.yml")
GUARD = "Require the rotation procedure for the bundled database password"


def _tasks():
    with open(SET_SINGLE) as f:
        return yaml.safe_load(f)


def _guard():
    return next(t for t in _tasks() if t.get("name") == GUARD)


def _fails(key, orchestrator="compose", force=False, ext_db=""):
    templar = Templar(loader=DataLoader(), variables={
        "_config_key": key,
        "instance_orchestrator": orchestrator,
        "force": force,
        "_set_ext_db": {"value": ext_db},
    })
    return all(templar.template(trust_as_template("{{ %s }}" % cond))
               for cond in _guard()["when"])


def test_guard_runs_before_key_validation():
    names = [t.get("name") for t in _tasks()]
    assert names.index("Read the external database flag") \
        < names.index(GUARD) < names.index("Validate key")


@pytest.mark.parametrize("kwargs, expected", [
    ({}, True),
    ({"ext_db": "false"}, True),
    ({"force": True}, False),
    ({"ext_db": "true"}, False),
    ({"ext_db": "True"}, False),
    ({"orchestrator": "kubernetes"}, False),
])
def test_guard_conditions(kwargs, expected):
    assert _fails("MYSQL_PASSWORD", **kwargs) is expected


def test_other_keys_are_not_affected():
    assert _fails("MW_SITE_SERVER") is False


def test_message_gives_both_rotation_steps():
    msg = _guard()["ansible.builtin.fail"]["msg"]
    assert "ALTER USER 'root'@'%'" in msg
    assert "ALTER USER 'root'@'localhost'" in msg
    assert "canasta maintenance exec" in msg and "-s db --stdin-file" in msg
    assert "canasta config set" in msg and "--force MYSQL_PASSWORD=" in msg
