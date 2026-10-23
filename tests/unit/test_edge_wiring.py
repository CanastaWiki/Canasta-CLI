"""Where the edge hooks into the instance lifecycle.

The edge must be in line before the instance's containers start, since its
Caddy joins the edge's network; delete must take the instance out; and the
Python fast path, which cannot render the edge, must hand edge instances to
Ansible while still layering the edge file into the compose commands it runs.
"""

import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from direct_commands import _helpers, lifecycle  # noqa: E402

TASKS = os.path.join(ROOT, "roles", "orchestrator", "tasks")


def _tasks(name):
    with open(os.path.join(TASKS, name)) as handle:
        return yaml.safe_load(handle)


def _compose_block(name):
    return next(t for t in _tasks(name) if "Docker Compose" in t.get("name", ""))["block"]


def _index(tasks, predicate):
    return next(i for i, t in enumerate(tasks) if predicate(t))


def test_start_syncs_the_edge_before_building_the_file_list():
    tasks = _compose_block("start.yml")
    sync = _index(tasks, lambda t: t.get("ansible.builtin.include_tasks") == "edge_sync.yml")
    build = _index(tasks, lambda t: t.get("name") == "Build compose file arguments")
    up = _index(tasks, lambda t: t.get("name") == "Start containers")
    assert sync < build < up


def test_start_layers_the_edge_file_before_the_user_override():
    build = next(t for t in _compose_block("start.yml")
                 if t.get("name") == "Build compose file arguments")
    files = build["ansible.builtin.set_fact"]["_compose_files"]
    assert files.index("docker-compose.edge.yml") < files.index("docker-compose.override.yml")


def test_destroy_takes_the_instance_out_of_the_edge():
    tasks = _compose_block("destroy.yml")
    down = _index(tasks, lambda t: t.get("name") == "Destroy containers and volumes")
    leave = _index(tasks, lambda t: t.get("ansible.builtin.include_tasks") == "edge_sync.yml")
    assert down < leave
    assert tasks[leave]["vars"] == {"edge_member": "absent"}


def test_python_compose_args_layer_the_edge_file(tmp_path):
    (tmp_path / "docker-compose.edge.yml").write_text("")
    (tmp_path / "docker-compose.override.yml").write_text("")
    assert _helpers._compose_file_args(str(tmp_path), "localhost") == [
        "-f", "docker-compose.yml",
        "-f", "docker-compose.edge.yml",
        "-f", "docker-compose.override.yml",
    ]


def test_fast_path_start_hands_edge_instances_to_ansible(monkeypatch):
    inst = {"id": "one", "path": "/srv/one", "host": "localhost"}
    monkeypatch.setattr(_helpers, "_resolve_instance", lambda args: ("one", inst))
    monkeypatch.setattr(_helpers, "_is_k8s", lambda inst: False)
    monkeypatch.setattr(_helpers, "_instance_has_sidecars", lambda inst: False)
    monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                        lambda inst: {})
    monkeypatch.setattr(_helpers, "_run_compose", lambda *a, **k: 1 / 0)
    assert lifecycle.cmd_start(None) is _helpers.FALLBACK


def test_edge_reloads_signal_haproxy_through_exec():
    # `docker kill` marks the container manually stopped, so the daemon would
    # not restart it.
    reloads = [t for t in _tasks("edge_sync.yml") if t.get("name", "").startswith("Reload the edge")]
    assert len(reloads) == 2
    for task in reloads:
        argv = task["ansible.builtin.command"]["argv"]
        assert argv[1:] == ["exec", "canasta-edge", "kill", "-USR2", "1"]


def test_the_edge_reloads_after_a_start_that_left_haproxy_running():
    # A start that only brings up acme.sh does not touch a running HAProxy.
    reload = next(t for t in _tasks("edge_sync.yml") if t.get("name") == "Reload the edge")
    assert "_edge_up is skipped" not in reload["when"]


def test_issuance_retries_on_the_edges_first_account():
    issue = next(t for t in _tasks("edge_sync.yml")
                 if t.get("name") == "Obtain the edge's Let's Encrypt certificates")
    assert "acme_registered" in issue["canasta_edge"]["retry"]
