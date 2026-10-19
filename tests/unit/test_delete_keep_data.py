"""`canasta delete --keep-data` removes the instance from the registry and
nothing else, so a Kubernetes instance can be replaced by a Compose one
under the same ID while its namespace and volumes stay as the way back."""

import os

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(ROOT, "roles", "delete", "tasks", "main.yml")


def _tasks():
    with open(TASKS) as f:
        return yaml.safe_load(f)


def test_keep_data_takes_the_deregister_only_path():
    tasks = _tasks()
    names = [t.get("name") for t in tasks]
    block = tasks[names.index("Deregister only")]
    assert "keep_data" in " ".join(str(c) for c in block["when"])
    assert block["block"][-1].get("ansible.builtin.meta") == "end_play"
    # Every teardown step comes after it, so the play ends before them.
    for step in ("Clean up container-owned files", "Remove Argo CD Application if present",
                 "Destroy containers and volumes", "Remove instance directory contents"):
        assert names.index(step) > names.index("Deregister only"), step


def test_keep_data_is_a_delete_option():
    with open(os.path.join(ROOT, "meta", "command_definitions.yml")) as f:
        defs = yaml.safe_load(f)
    cmds = defs["commands"] if isinstance(defs, dict) else defs
    delete = next(c for c in cmds if c["name"] == "delete")
    assert any(p["name"] == "keep_data" and p["type"] == "bool" for p in delete["parameters"])
