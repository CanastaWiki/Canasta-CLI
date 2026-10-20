"""Every Compose instance is behind the HAProxy edge: create puts new ones
there, and upgrade moves existing ones.

Create refuses only what cannot run the edge: rootless Podman without the
operator's systemd user session, which gives the edge its ports. The real
preflight task runs here.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
PREFLIGHT = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml")
UPGRADE = os.path.join(REPO_ROOT, "playbooks", "upgrade.yml")


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _preflight(tmp_path, **variables):
    with open(PREFLIGHT) as f:
        tasks = list(_walk(yaml.safe_load(f)))
    task = next(t for t in tasks
                if t.get("name") == "Refuse rootless Podman without a systemd user session")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": dict(variables, ansible_python_interpreter=sys.executable),
        "tasks": [task],
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


@pytest.mark.parametrize("variables,refused", [
    ({"compose_command": "docker compose"}, False),
    ({"compose_command": "podman-compose", "_preflight_user_systemd": {"rc": 0}}, False),
    ({"compose_command": "podman-compose", "_preflight_user_systemd": {"rc": 1}}, True),
    ({"compose_command": "podman-compose"}, True),
])
def test_create_refuses_only_podman_without_a_user_session(tmp_path, variables, refused):
    proc = _preflight(tmp_path, **variables)
    assert (proc.returncode != 0) is refused, proc.stdout
    if refused:
        assert "systemd user session" in proc.stdout, proc.stderr


def test_upgrade_moves_hosts_before_instances_upgrade():
    with open(UPGRADE) as f:
        tasks = [t for t in _walk(yaml.safe_load(f)) if t.get("name")]
    names = [t["name"] for t in tasks]
    assert names.index("Move each host's instances behind the edge") < \
        names.index("Upgrade instances")
    move = tasks[names.index("Move each host's instances behind the edge")]
    assert move["ansible.builtin.include_tasks"] == "_edge_convert_host.yml"


def test_compose_instances_get_the_snippet_file_without_clobbering():
    with open(os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "init_config.yml")) as f:
        top = yaml.safe_load(f)
    compose = next(t for t in top if t.get("name") == "Compose-specific init")
    assert compose["when"] == "not (instance_is_k8s | bool)"
    tasks = {t.get("name"): t for t in compose["block"]}
    copy = tasks["Create haproxy-backend.cfg from template (no-clobber)"]["ansible.builtin.copy"]
    assert copy["dest"] == "{{ instance_path }}/config/haproxy-backend.cfg"
    assert copy["force"] is False
    assert os.path.isfile(os.path.join(REPO_ROOT, "instance_template", "config", "haproxy-backend.cfg"))


@pytest.mark.parametrize("running,expected", [
    ("db\nweb\ncaddy\nvarnish", True),
    ("db\nweb\nvarnish", False),
    ("", False),
])
def test_upgrade_remembers_whether_caddy_was_running(tmp_path, running, expected):
    with open(os.path.join(REPO_ROOT, "playbooks", "_edge_convert_prepare.yml")) as f:
        tasks = list(_walk(yaml.safe_load(f)))
    remember = next(t for t in tasks
                    if t.get("name") == "Remember whether the instance's Caddy was running")
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_id": "one", "_running_services": running,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [remember, {"ansible.builtin.copy": {
            "content": "{{ _edge_was_running | to_json }}", "dest": str(out)}}],
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(out.read_text()) == {"one": expected}


def test_a_failed_move_is_counted_once(tmp_path):
    # The moved instances are skipped, and the failure that reports them
    # must not take them off the upgraded count a second time.
    with open(os.path.join(REPO_ROOT, "playbooks", "_edge_convert_host.yml")) as f:
        record = next(t for t in _walk(yaml.safe_load(f))
                      if t.get("name") == "Record the failed move to the edge")
    with open(UPGRADE) as f:
        report = next(t for t in _walk(yaml.safe_load(f))
                      if t.get("name") == "Report upgrade complete")
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "_upgrade_instances": {"instances": {i: {} for i in ("a", "b", "c", "d", "fm")}},
            "_edge_plan": {"convert": ["fm"]},
            "_edge_host_name": "localhost",
            "ansible_failed_result": {"msg": "The rendered edge configuration is invalid.\n"},
        },
        "tasks": [record, dict(report, register="_report"),
                  {"ansible.builtin.copy": {
                      "content": "{{ {'msg': _report.msg, 'failures': _upgrade_failures}"
                                 " | to_json }}",
                      "dest": str(out)}}],
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(out.read_text())
    assert result["msg"].startswith("Upgraded 4 instance(s).")
    assert result["failures"] == [
        "moving fm behind the HAProxy edge on localhost failed; fm is back on"
        " its own Caddy as before the upgrade, and not upgraded. Run the upgrade"
        " again once this is fixed: The rendered edge configuration is invalid."]


def _dry_run(tmp_path, instances):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"ansible_python_interpreter": sys.executable, "canasta_root": REPO_ROOT,
                 "_edge_host_name": "localhost",
                 "_edge_host_instances": [{"key": i, "value": {"path": str(p)}}
                                          for i, p in instances.items()],
                 "_upgrade_default_compose_command": "docker compose",
                 "_upgrade_default_inspect_command": "true"},
        "tasks": [{"ansible.builtin.include_tasks":
                   os.path.join(REPO_ROOT, "playbooks", "_edge_dry_run_host.yml")}],
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def _instance(tmp_path, name, url):
    inst = tmp_path / name
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("")
    (inst / "config" / "wikis.yaml").write_text(
        yaml.safe_dump({"wikis": [{"id": "main", "url": url, "name": "Main"}]}))
    return inst


def test_dry_run_reports_the_instances_that_would_move(tmp_path):
    out = _dry_run(tmp_path, {"one": _instance(tmp_path, "one", "one.example.com"),
                              "two": _instance(tmp_path, "two", "two.example.com")})
    assert "Dry run - would move one, two on localhost behind the HAProxy edge." in out
    assert not (tmp_path / "one" / "docker-compose.edge.yml").exists()


def test_dry_run_reports_a_refused_host(tmp_path):
    out = _dry_run(tmp_path, {"one": _instance(tmp_path, "one", "same.example.com"),
                              "two": _instance(tmp_path, "two", "same.example.com")})
    assert "Dry run - would not upgrade one, two on localhost:" in out
    assert "both serve same.example.com" in out


def _rootless_messages(tmp_path, playbook_file, task_name):
    with open(os.path.join(REPO_ROOT, "playbooks", playbook_file)) as f:
        task = next(t for t in _walk(yaml.safe_load(f)) if t.get("name") == task_name)
    task = {k: v for k, v in task.items() if k != "when"}
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "_edge_host_name": "localhost",
            "_edge_plan": {"convert": ["bare", "rated"], "rootless": ["bare", "rated"],
                           "rated": ["rated"], "refuse": None, "held": []},
        },
        "tasks": [dict(task, register="_msgs"),
                  {"ansible.builtin.copy": {
                      "content": "{{ (_msgs.results | default([_msgs]))"
                                 " | map(attribute='msg') | list | to_json }}",
                      "dest": str(out)}}],
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text())


def test_the_upgrade_warning_leaves_out_limits_an_instance_sets(tmp_path):
    bare, rated = _rootless_messages(tmp_path, "_edge_convert_host.yml",
                                     "Warn about instances moving on rootless Docker")
    assert "no per-client rate limits" in bare and "CANASTA_EDGE_RATE_DYNAMIC" in bare
    assert "no per-client rate limits" not in rated
    assert "CANASTA_EDGE_RATE_DYNAMIC" not in rated
    assert "canasta install docker-rootless" in rated
    assert "daemon.json" not in bare + rated


def test_the_dry_run_names_only_instances_without_limits(tmp_path):
    [msg] = _rootless_messages(tmp_path, "_edge_dry_run_host.yml",
                               "Report the instances that would move behind the edge")
    assert msg.startswith("Dry run - would move bare, rated on localhost")
    assert "(bare on rootless Docker, with no per-client rate limits)" in msg
