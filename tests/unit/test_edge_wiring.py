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
    return _tasks(name)


def _index(tasks, predicate):
    return next(i for i, t in enumerate(tasks) if predicate(t))


def test_start_syncs_the_edge_before_building_the_file_list():
    tasks = _compose_block("start.yml")
    sync = _index(tasks, lambda t: t.get("ansible.builtin.include_tasks") == "edge_sync.yml")
    build = _index(tasks, lambda t: t.get("name") == "Build compose file arguments")
    up = _index(tasks, lambda t: t.get("name") == "Start containers")
    assert sync < build < up


def test_start_layers_the_edge_file_before_the_user_override():
    layers = next(t for t in _tasks("compose_file_args.yml")
                  if t.get("name") == "Check for the compose layers")["loop"]
    assert layers.index("docker-compose.edge.yml") < layers.index("docker-compose.override.yml")


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


def _role_tasks(*parts):
    with open(os.path.join(ROOT, *parts)) as handle:
        return yaml.safe_load(handle)


LEARN = {"name": "orchestrator", "tasks_from": "edge_learn.yml"}


def test_start_and_restart_learn_the_special_names_once_the_containers_are_up():
    for name in ("start.yml", "restart.yml"):
        tasks = _role_tasks("roles", "instance_lifecycle", "tasks", name)
        up = _index(tasks, lambda t: t.get("name") == "Start containers")
        learn = _index(tasks, lambda t: t.get("ansible.builtin.include_role") == LEARN)
        assert up < learn and "when" not in tasks[learn]


def test_create_learns_the_special_names_after_the_wiki_is_set_up():
    tasks = _role_tasks("roles", "create", "tasks", "main.yml")
    last_start = _index(tasks, lambda t: t.get("name") == "Start containers with full config")
    update = _index(tasks, lambda t: t.get("name") == "Run update.php for the imported wiki")
    learn = _index(tasks, lambda t: t.get("ansible.builtin.include_role") == LEARN)
    assert last_start < update < learn and "when" not in tasks[learn]


def test_upgrade_learns_the_special_names_of_instances_left_running():
    tasks = _role_tasks("playbooks", "_upgrade_single.yml")
    upgrade = _index(tasks, lambda t: t.get("ansible.builtin.include_role") == {"name": "upgrade"})
    learn = _index(tasks, lambda t: t.get("ansible.builtin.include_role") == LEARN)
    assert upgrade < learn
    assert "_was_running | default(false) | bool" in tasks[learn]["when"]
    assert "not (_upgrade_restart_failed | default(false) | bool)" in tasks[learn]["when"]


def _probes(task):
    return (task.get("ansible.builtin.include_role") or {}).get("tasks_from") == "edge_probe.yml"


def test_start_and_restart_report_wikis_the_edge_does_not_answer_for():
    for name, report in (("start.yml", "Report started"), ("restart.yml", "Report restarted")):
        tasks = _role_tasks("roles", "instance_lifecycle", "tasks", name)
        assert _index(tasks, _probes) > _index(tasks, lambda t: t.get("name") == report)


def test_upgrade_reports_wikis_of_the_running_instances_it_upgraded():
    tasks = _role_tasks("playbooks", "_upgrade_single.yml")
    probe = tasks[_index(tasks, _probes)]
    assert tasks[-2] is probe
    assert tasks[-1]["name"] == "Record an upgraded instance that is not serving"
    assert "not (_upgrade_skip | bool)" in probe["when"]
    assert any("_was_running" in w for w in probe["when"])
    assert any("_upgrade_restart_failed" in w for w in probe["when"])


def _fast_stop(monkeypatch, tmp_path, host="localhost"):
    calls = []
    inst = {"path": str(tmp_path / "one"), "orchestrator": "compose", "host": host}
    monkeypatch.setattr(_helpers, "_resolve_instance", lambda args: ("one", inst))
    monkeypatch.setattr(_helpers, "_instance_has_sidecars", lambda inst: False)
    monkeypatch.setattr(_helpers, "_resolve_inspect_cmd", lambda inst: "docker")
    monkeypatch.setattr(_helpers, "_get_config_dir", lambda: str(tmp_path / "config"))
    monkeypatch.setattr(_helpers, "_sync_compose_profiles", lambda inst: {})
    monkeypatch.setattr(_helpers, "_compose_containers_up", lambda inst: True)
    monkeypatch.setattr(_helpers, "_run_compose",
                        lambda i, inst, action, **kw: calls.append(action) or 0)
    return calls


def test_fast_path_stop_refuses_while_the_edge_is_unwritable(monkeypatch, tmp_path, capsys):
    calls = _fast_stop(monkeypatch, tmp_path)
    checked = []

    def unwritable(edge_dir, instance_path=None):
        checked.append((edge_dir, instance_path))
        raise lifecycle.canasta_edge_render.EdgeError("cannot write the edge")
    monkeypatch.setattr(lifecycle.canasta_edge_writable, "check_writable", unwritable)
    args = type("Args", (), {"id": "one"})()
    assert lifecycle.cmd_stop(args) == 1
    assert checked == [(str(tmp_path / "config" / "edge"), str(tmp_path / "one"))]
    assert calls == []
    assert "cannot write the edge" in capsys.readouterr().err


def test_fast_path_stop_runs_when_the_edge_is_writable(monkeypatch, tmp_path):
    calls = _fast_stop(monkeypatch, tmp_path)
    args = type("Args", (), {"id": "one"})()
    assert lifecycle.cmd_stop(args) == 0
    assert calls == [["down", "--remove-orphans"]]


def test_fast_path_stop_hands_remote_instances_to_ansible(monkeypatch, tmp_path):
    # The edge is on the instance's host, where Ansible makes the check.
    calls = _fast_stop(monkeypatch, tmp_path, host="remote.example.com")
    args = type("Args", (), {"id": "one"})()
    assert lifecycle.cmd_stop(args) is _helpers.FALLBACK
    assert calls == []
