"""Every build from source records the base image it was built on.

`canasta version` reports a build-from instance's image by its local tag,
which says nothing about the Canasta release underneath it. The effective
BASE_IMAGE that build_from_source.yml resolves is stored as buildBase in
the controller's registry record by both paths that build: create and
upgrade's rebuild.
"""

import os

import jinja2
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BUILD_FROM = os.path.join(
    REPO_ROOT, "roles", "imagebuild", "tasks", "build_from_source.yml")
CREATE_REGISTER = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "_register.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")

RECORD = "Record the base image of the rebuilt image"
REBUILD = "Rebuild image from source"
CONTROLLER_VARS = {
    "ansible_connection": "local",
    "ansible_python_interpreter": "{{ ansible_playbook_python }}",
}


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _by_name(tasks, name):
    return next(t for t in tasks if t.get("name") == name)


def _render(expr, **context):
    env = jinja2.Environment()
    return env.from_string(expr).render(omit="__omit__", **context)


def test_the_build_resolves_the_effective_base():
    names = [t.get("name") for t in _tasks(BUILD_FROM)]
    assert names.index("Resolve effective BASE_IMAGE") < names.index(
        "Build Canasta image from source")


def test_create_records_the_base_on_the_controller():
    task = _by_name(_tasks(CREATE_REGISTER),
                    "Register instance in controller config")
    assert task["delegate_to"] == "canasta_controller"
    assert task["vars"] == CONTROLLER_VARS
    expr = task["canasta_registry"]["build_base"]
    assert _render(expr, build_from="/src",
                   _effective_base_image="canasta-base:local") == (
        "canasta-base:local")


def test_create_without_build_from_records_no_base():
    task = _by_name(_tasks(CREATE_REGISTER),
                    "Register instance in controller config")
    expr = task["canasta_registry"]["build_base"]
    assert _render(expr) == "__omit__"
    assert _render(expr, build_from="") == "__omit__"


def test_upgrade_records_the_base_of_each_rebuild():
    tasks = _tasks(UPGRADE_MAIN)
    names = [t.get("name") for t in tasks]
    assert names.index(REBUILD) < names.index(RECORD)
    task = _by_name(tasks, RECORD)
    assert task["when"] == "_upgrade_rebuild | bool"
    assert task["delegate_to"] == "canasta_controller"
    assert task["vars"] == CONTROLLER_VARS
    reg = task["canasta_registry"]
    assert reg["state"] == "update"
    assert reg["id"] == "{{ instance_id }}"
    assert reg["build_base"] == "{{ _effective_base_image }}"
