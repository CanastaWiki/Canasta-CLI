"""Every Compose command layers the same files.

compose_file_args.yml builds the -f list for start, stop, destroy and the
my.cnf database recreate, and must match _compose_file_args, which the
Python fast path uses. A layer left out of `down -v` keeps its volumes; one
left out of `up` turns its services into orphans.
"""

import os
import subprocess
import sys

import pytest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from direct_commands import _helpers  # noqa: E402

TASKS = os.path.join(ROOT, "roles", "orchestrator", "tasks")
LAYERS = ("docker-compose.sidecars.yml", "docker-compose.web-env.yml",
          "docker-compose.edge.yml", "docker-compose.override.yml")


def _ansible_files(tmp_path, inst, devmode):
    out = tmp_path / "files.yml"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(inst), "instance_devmode": devmode,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [
            {"ansible.builtin.include_tasks": os.path.join(TASKS, "compose_file_args.yml")},
            {"ansible.builtin.copy": {"content": "{{ _compose_files | to_yaml }}",
                                      "dest": str(out)}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return yaml.safe_load(out.read_text())


@pytest.mark.parametrize("present,devmode", [
    ((), False),
    (LAYERS, True),
    (("docker-compose.edge.yml", "docker-compose.override.yml"), False),
])
def test_ansible_and_fast_path_layer_the_same_files(tmp_path, present, devmode):
    inst = tmp_path / "inst"
    inst.mkdir()
    for name in present:
        (inst / name).write_text("services: {}\n")
    expected = _helpers._compose_file_args(
        str(inst), "localhost", devmode, include_sidecars=True)
    assert _ansible_files(tmp_path, inst, devmode) == expected


def _file_list_parts(node):
    """The stat paths and set_fact values in a task tree."""
    if isinstance(node, dict):
        if "ansible.builtin.stat" in node:
            yield str(node["ansible.builtin.stat"].get("path", ""))
            yield str(node.get("loop", ""))
        if "ansible.builtin.set_fact" in node:
            yield str(node["ansible.builtin.set_fact"])
        for value in node.values():
            yield from _file_list_parts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _file_list_parts(value)


def _includes(node):
    if isinstance(node, dict):
        if node.get("ansible.builtin.include_tasks") == "compose_file_args.yml":
            yield node
        for value in node.values():
            yield from _includes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _includes(value)


@pytest.mark.parametrize("name", [
    "start.yml", "stop.yml", "destroy.yml", "restart_db_for_mycnf.yml"])
def test_compose_commands_use_the_shared_file_list(name):
    with open(os.path.join(TASKS, name)) as handle:
        tasks = yaml.safe_load(handle)
    assert list(_includes(tasks))
    for text in _file_list_parts(tasks):
        assert not any(layer in text for layer in LAYERS), (
            "%s builds its own -f list: %s" % (name, text))
