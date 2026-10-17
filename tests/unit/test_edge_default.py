"""New Compose instances go behind the HAProxy edge by default, and upgrade
moves existing ones there.

The create preflight decides, since it runs before anything binds ports: an
instance goes behind the edge unless the -e env file says otherwise, the
instance cannot be routed by hostname, or the env file asks for its own
ports or for Caddy to sit behind another proxy. The real preflight task runs
here against a temp env file.
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


def _decide(tmp_path, env_lines, domain="example.com", **extra):
    with open(PREFLIGHT) as f:
        tasks = list(_walk(yaml.safe_load(f)))
    names = ("Read the custom env file for port overrides",
             "Resolve the ports this instance will bind")
    chosen = [next(t for t in tasks if t.get("name") == n) for n in names]
    envfile = tmp_path / "custom.env"
    envfile.write_text("".join(line + "\n" for line in env_lines))
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"envfile": str(envfile), "domain_name": domain,
                      "ansible_python_interpreter": sys.executable}, **extra),
        "tasks": chosen + [{"ansible.builtin.copy": {
            "content": "{{ create_behind_edge | bool | to_json }}", "dest": str(out)}}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,canasta_controller,", "-l", "localhost", str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text())


@pytest.mark.parametrize("env_lines,domain,extra,expected", [
    ([], "example.com", {}, True),
    ([], "localhost", {}, True),
    ([], "wiki.localhost", {}, True),
    ([], "192.0.2.4", {}, True),
    ([], "example.com", {"skip_tls": True}, True),
    (["HTTPS_PORT=8443"], "example.com", {}, True),
    (["HTTP_PORT=8080"], "example.com", {}, True),
    (["CADDY_AUTO_HTTPS=off"], "example.com", {}, True),
    (["HTTP_PORT=80", "HTTPS_PORT=443"], "example.com", {}, True),
    (["CANASTA_ENABLE_EDGE=false"], "example.com", {}, False),
    (["CANASTA_ENABLE_EDGE=true", "HTTPS_PORT=8443"], "example.com", {}, True),
    ([], "example.com", {"compose_command": "podman-compose"}, False),
    ([], "example.com", {"compose_command": "podman-compose",
                         "_preflight_user_systemd": {"rc": 0}}, True),
    ([], "example.com", {"compose_command": "podman-compose",
                         "_preflight_user_systemd": {"rc": 1}}, False),
    ([], "example.com", {"_preflight_docker_info": {"stdout": '["name=rootless"]'}}, False),
    ([], "example.com", {"_preflight_docker_info": {"stdout": '["name=seccomp"]'}}, True),
    (["CANASTA_ENABLE_EDGE=true"], "example.com",
     {"_preflight_docker_info": {"stdout": '["name=rootless"]'}}, True),
    (["CANASTA_ENABLE_EDGE=true"], "example.com", {"compose_command": "podman-compose"}, True),
])
def test_create_decides_whether_to_use_the_edge(tmp_path, env_lines, domain, extra, expected):
    assert _decide(tmp_path, env_lines, domain, **extra) is expected


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
