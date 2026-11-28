"""The shared image reclaim keeps images a stopped instance still needs.

The removal loop relied on Docker refusing to delete an in-use image. That
guard only covers images a container object references, and `canasta stop`
runs `docker compose down`, which removes the containers. A stopped instance
therefore had nothing holding its image, and `canasta upgrade --purge`
deleted the image the upgrade had just refreshed it to.

The pins recorded in each instance's .env are the durable record, so they
decide what stays.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS_DIR = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
TASKS = os.path.join(TASKS_DIR, "image_reclaim.yml")
RUNTIME_TASKS = os.path.join(TASKS_DIR, "_image_reclaim_runtime.yml")


def _load():
    """The reclaim's tasks, with the per-runtime file it includes."""
    tasks = []
    for path in (TASKS, RUNTIME_TASKS):
        with open(path) as f:
            tasks.extend(yaml.safe_load(f))
    return tasks


def _flatten(tasks):
    """Every task, including those nested in a block."""
    out = []
    for task in tasks:
        out.append(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                out.extend(_flatten(task[key]))
    return out


def _by_name(name):
    for task in _flatten(_load()):
        if task.get("name") == name:
            return task
    raise AssertionError("task not found: %s" % name)


def test_removal_set_excludes_pinned_tags():
    fact = _by_name("Select the tags no instance on this host is pinned to")
    expr = str(fact["ansible.builtin.set_fact"]["_reclaim_removable"])
    assert "difference(_reclaim_pinned)" in expr


def test_pins_come_from_every_instance_on_the_host_not_from_containers():
    read = _by_name("Read the images each instance is pinned to")
    assert read["canasta_env"]["state"] == "read_all"
    query = _by_name("Find every registered instance")
    assert query["canasta_registry"]["state"] == "query_all"
    # Not host-filtered: --host is often an SSH alias that does not match the
    # registry's canonical user@fqdn, which silently emptied the pin set.
    assert "filter_host" not in query["canasta_registry"]
    # The registry lives on the controller, never on the target.
    assert query["delegate_to"] == "canasta_controller"


def test_removal_loops_over_the_filtered_set():
    remove = _by_name("Remove Canasta image tags no instance needs")
    assert remove["loop"] == "{{ _reclaim_removable }}"
    # No --force, so a running instance's tag survives a missed pin too.
    assert "--force" not in remove["ansible.builtin.command"]["cmd"]
    assert " -f " not in remove["ansible.builtin.command"]["cmd"]


def _commands():
    """Every shell/command string the reclaim runs."""
    out = []
    for task in _flatten(_load()):
        for module in ("ansible.builtin.command", "ansible.builtin.shell"):
            if module in task:
                out.append(str(task[module].get("cmd", "")))
    return out


def test_no_command_touches_volumes():
    for cmd in _commands():
        assert "volume" not in cmd, cmd
        assert "--volumes" not in cmd, cmd
        assert "system prune" not in cmd, cmd


@pytest.mark.parametrize("name", [
    "Remove Canasta image tags no instance needs",
    "Prune dangling images (sidecar rebuild orphans)",
])
def test_every_destructive_task_is_gated_on_dry_run(name):
    cond = str(_by_name(name).get("when"))
    assert "reclaim_dry_run" in cond, name
    assert "not (" in cond, name


def test_registry_gc_include_is_not_wrapped_in_a_conditional_block():
    """Block scoping breaks set_fact propagation out of an included file."""
    for task in _load():
        if "block" not in task:
            continue
        for child in _flatten(task["block"]):
            assert "ansible.builtin.include_tasks" not in child, task.get("name")


class TestPinSetCoversEveryImageKey:
    """CANASTA_IMAGE alone left an instance's Caddy image unprotected."""

    def test_all_image_valued_env_keys_are_pinned(self):
        keys = _by_name("Name the .env keys that hold an image reference")[
            "ansible.builtin.set_fact"]["_reclaim_image_keys"]
        assert set(keys) >= {"CANASTA_IMAGE", "CANASTA_CADDY_IMAGE"}

    def test_pins_are_read_from_every_instance_on_the_host(self):
        read = _by_name("Read the images each instance is pinned to")
        assert read["canasta_env"]["state"] == "read_all"
