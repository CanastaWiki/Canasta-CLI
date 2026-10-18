"""`canasta add -u` without a port gets the instance's port.

MediaWiki finds the wiki by matching the request's host:port against
wikis.yaml, and create writes the main wiki's URL with the instance's
non-default port. A URL added without that port never matched, so the
new wiki 404ed. The real add.yml tasks run here against a temp .env.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")

NAMES = ("Read the instance's ports from .env", "Parse URL into domain and path")


def _tasks():
    with open(ADD) as f:
        found = {t.get("name"): t for t in yaml.safe_load(f)}
    return [found[n] for n in NAMES]


def _parse(tmp_path, url, env_lines):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text("".join(line + "\n" for line in env_lines))
    out = tmp_path / "out.json"
    tasks = _tasks() + [{
        "ansible.builtin.copy": {
            "content": "{{ {'domain': _add_domain, 'path': _add_path} | to_json }}",
            "dest": str(out),
            "mode": "0644",
        },
    }]
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "url": url,
                 "ansible_python_interpreter": sys.executable},
        "tasks": tasks,
    }]))
    empty = tmp_path / "collections"
    empty.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_COLLECTIONS_PATH=str(empty),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(out.read_text())
    return result["domain"].strip(), result["path"].strip()


@pytest.mark.parametrize("url,env_lines,expected", [
    # The reported case: HTTPS on a non-default port.
    ("localhost/w2", ["HTTPS_PORT=8443"], ("localhost:8443", "w2")),
    # HTTP-only instances are served on HTTP_PORT.
    ("localhost/w2", ["TLS_MODE=off", "HTTP_PORT=8081",
                      "HTTPS_PORT=8443"], ("localhost:8081", "w2")),
    # A farm's other hostnames are served on the same port.
    ("docs.example.com", ["HTTPS_PORT=8443"], ("docs.example.com:8443", "")),
    # A port the user gives is kept.
    ("localhost:9000/w2", ["HTTPS_PORT=8443"], ("localhost:9000", "w2")),
    # Default ports are never written into the URL.
    ("localhost/w2", ["HTTPS_PORT=443"], ("localhost", "w2")),
    ("localhost/w2", [], ("localhost", "w2")),
    ("localhost/w2", ["TLS_MODE=off", "HTTP_PORT=80",
                      "HTTPS_PORT=8443"], ("localhost", "w2")),
    ("example.com", ["HTTPS_PORT="], ("example.com", "")),
])
def test_url_gets_the_instance_port(tmp_path, url, env_lines, expected):
    assert _parse(tmp_path, url, env_lines) == expected


def test_k8s_values_domains_never_carry_a_port():
    with open(ADD) as f:
        text = f.read()
    assert "union([_add_domain | trim | regex_replace(':[0-9]+$', '')])" in text
