"""`canasta upgrade` moves a running instance onto a new managed Caddy image.

The restart decision counted only the Canasta image and stack changes, so a
bump of canasta_caddy_plugin_image left instances on the old Caddy until
their next start: Compose kept the old container, and Kubernetes never
reconciled caddy.image in values.yaml.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHECK = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "check_caddy_image.yml")
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")

PLUGIN = "ghcr.io/canastawiki/canasta-caddy:2.11.9"
PREFIX = "ghcr.io/canastawiki/canasta-caddy:"


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _named(tasks, name):
    for t in tasks:
        if t.get("name") == name:
            return t
        found = _named(t.get("block", []), name) if "block" in t else None
        if found:
            return found
    return None


def _render(expr, variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _when_holds(task, variables):
    return all(
        str(_render("{{ %s }}" % cond, variables)).strip() == "True"
        for cond in task["when"])


@pytest.mark.parametrize("current, moves", [
    ("ghcr.io/canastawiki/canasta-caddy:2.11.3", True),
    (PLUGIN, False),
    ("", False),
    ("docker.io/library/caddy:2.10.2-alpine", False),
    ("registry.example.com/my-caddy:1.0", False),
])
def test_only_an_older_managed_tag_moves_in_env(current, moves):
    task = _named(_load(CHECK), "Move .env to the current managed Caddy image")
    assert task["canasta_env"]["value"] == "{{ canasta_caddy_plugin_image }}"
    assert _when_holds(task, {
        "_upgrade_caddy_env": {"value": current},
        "_upgrade_caddy_managed_prefix": PREFIX,
        "canasta_caddy_plugin_image": PLUGIN,
    }) is moves


OLD = "ghcr.io/canastawiki/canasta-caddy:2.11.3"


@pytest.mark.parametrize("before, env_set_changed, running, changed", [
    (PLUGIN, True, [], True),
    (PLUGIN, False, [OLD], True),
    # A stopped instance whose .env the stack-file write already moved.
    (OLD, False, [], True),
    (PLUGIN, False, [PLUGIN], False),
    ("", False, ["caddy:2.10.2-alpine"], False),
    ("registry.example.com/my-caddy:1.0", False,
     ["registry.example.com/my-caddy:1.0"], False),
    ("", False, [], False),
])
def test_compose_change_signal(before, env_set_changed, running, changed):
    task = _named(_load(CHECK), "Note whether the Caddy image changes (Compose)")
    expr = task["ansible.builtin.set_fact"]["_upgrade_caddy_image_changed"]
    out = _render(expr, {
        "_upgrade_caddy_env_before": {"value": before},
        "_upgrade_caddy_env_set": {"changed": env_set_changed},
        "_upgrade_caddy_running": {"stdout_lines": running},
        "_upgrade_caddy_managed_prefix": PREFIX,
        "canasta_caddy_plugin_image": PLUGIN,
    })
    assert str(out).strip() == str(changed)


def test_running_check_only_for_a_running_instance():
    task = _named(_load(CHECK), "Read the image the running caddy container uses")
    assert task["when"] == "_was_running | bool"
    assert task["changed_when"] is False and task["failed_when"] is False
    assert "com.docker.compose.service=caddy" in task["ansible.builtin.command"]["cmd"]


def test_signal_is_reset_per_instance():
    reset = _load(CHECK)[0]
    assert reset["ansible.builtin.set_fact"]["_upgrade_caddy_image_changed"] is False


def test_signal_feeds_the_restart_and_the_gitops_stage():
    tasks = _load(MAIN)
    names = [t.get("name") for t in tasks]
    check = names.index("Check whether the managed Caddy image changes")
    assert tasks[check]["ansible.builtin.include_tasks"] == "check_caddy_image.yml"
    # After the chart refresh that may rewrite values.yaml, and before the
    # decision it feeds.
    assert names.index("Update configured image tag") < check < names.index(
        "Decide whether a restart is needed")
    restart = tasks[names.index("Decide whether a restart is needed")][
        "ansible.builtin.set_fact"]["_restart_needed"]
    assert "_upgrade_caddy_image_changed" in restart
    pending = _named(tasks, "Decide whether the instance awaits a gitops push")[
        "ansible.builtin.set_fact"]["_upgrade_gitops_pending"]
    assert "_upgrade_caddy_image_changed" in pending


def test_compose_image_is_read_before_the_stack_files_are_written():
    # Writing the stack files runs the profile sync, which moves
    # CANASTA_CADDY_IMAGE itself; only the earlier value shows the change.
    tasks = _load(MAIN)
    names = [t.get("name") for t in tasks]
    before = names.index("Record the Caddy image before the stack files are rewritten")
    read = tasks[before]
    assert read["register"] == "_upgrade_caddy_env_before"
    assert read["canasta_env"]["key"] == "CANASTA_CADDY_IMAGE"
    assert read["when"] == "not (instance_is_k8s | bool)"
    writes = [i for i, t in enumerate(tasks)
              if t.get("ansible.builtin.include_role", {}).get("tasks_from")
              in ("write_stack_files.yml", "init_config.yml")]
    assert writes and before < min(writes)
