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
    ([], "localhost", {}, False),
    ([], "wiki.localhost", {}, False),
    ([], "192.0.2.4", {}, False),
    ([], "example.com", {"skip_tls": True}, False),
    (["HTTPS_PORT=8443"], "example.com", {}, False),
    (["HTTP_PORT=8080"], "example.com", {}, False),
    (["CADDY_AUTO_HTTPS=off"], "example.com", {}, False),
    (["HTTP_PORT=80", "HTTPS_PORT=443"], "example.com", {}, True),
    (["CANASTA_ENABLE_EDGE=false"], "example.com", {}, False),
    (["CANASTA_ENABLE_EDGE=true", "HTTPS_PORT=8443"], "example.com", {}, True),
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
