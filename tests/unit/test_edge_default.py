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
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
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
        assert "systemd user session" in proc.stdout


def test_upgrade_moves_hosts_before_instances_upgrade():
    with open(UPGRADE) as f:
        tasks = [t for t in _walk(yaml.safe_load(f)) if t.get("name")]
    names = [t["name"] for t in tasks]
    assert names.index("Move each host's instances behind the edge") < \
        names.index("Upgrade instances")
    move = tasks[names.index("Move each host's instances behind the edge")]
    assert move["ansible.builtin.include_tasks"] == "_edge_convert_host.yml"


def test_every_instance_gets_the_snippet_file_without_clobbering():
    with open(os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "init_config.yml")) as f:
        tasks = {t.get("name"): t for t in _walk(yaml.safe_load(f))}
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
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(out.read_text()) == {"one": expected}
