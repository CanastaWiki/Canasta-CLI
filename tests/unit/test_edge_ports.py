"""Ports and URLs for instances behind the HAProxy edge.

The edge serves each instance on its own HTTP_PORT and HTTPS_PORT, over
plain HTTP when TLS_MODE is off, so wiki URLs keep their ports and
those keys stay settable. The edge, for its part, owns the ports it serves.
The real task files run here against temp instances.
"""

import datetime
import json
import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers, doctor  # noqa: E402

COMMON = os.path.join(REPO_ROOT, "roles", "common")
CONFIG_TASKS = os.path.join(REPO_ROOT, "roles", "config", "tasks")
ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
ENVFILE = os.path.join(REPO_ROOT, "roles", "create", "tasks", "_envfile.yml")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")


def _instance(tmp_path, env, url="example.com"):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("".join("%s=%s\n" % kv for kv in env.items()))
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump(
        {"wikis": [{"id": "main", "url": url, "name": "Main"}]}))
    return inst


def _play(tmp_path, tasks, extra_vars, expect_fail=False):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, instance_orchestrator="compose",
                     canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable, **extra_vars),
        "tasks": tasks,
    }]))
    empty = tmp_path / "collections"
    empty.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_COLLECTIONS_PATH=str(empty),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    out = proc.stdout + proc.stderr
    if expect_fail:
        assert proc.returncode != 0, out
    else:
        assert proc.returncode == 0, out
    return out


def _env(inst):
    return dict(line.split("=", 1) for line in (inst / ".env").read_text().splitlines()
                if "=" in line)


def _url(inst):
    return yaml.safe_load((inst / "config" / "wikis.yaml").read_text())["wikis"][0]["url"]


def _tasks(path, *names):
    with open(path) as f:
        found = {t.get("name"): t for t in yaml.safe_load(f)}
    return [found[n] for n in names]


# --- config set ------------------------------------------------------------- #

def test_port_and_tls_keys_are_not_blocked_behind_the_edge():
    with open(os.path.join(CONFIG_TASKS, "_validate_key.yml")) as f:
        assert "behind the edge" not in f.read()


# --- create ------------------------------------------------------------------- #

def _create_domain(tmp_path, env, domain="example.com", **extra):
    inst = _instance(tmp_path, env)
    out = tmp_path / "out.json"
    tasks = [{"ansible.builtin.include_tasks": ENVFILE},
             {"ansible.builtin.copy": {
                 "content": "{{ {'domain': _domain_with_port, 'http_only': _http_only}"
                            " | to_json }}",
                 "dest": str(out), "mode": "0644"}}]
    _play(tmp_path, tasks, dict({"instance_path": str(inst), "domain_name": domain},
                                **extra))
    return json.loads(out.read_text())


def test_create_behind_the_edge_keeps_the_port(tmp_path):
    result = _create_domain(tmp_path, {"HTTPS_PORT": "8443"})
    assert result["domain"].strip() == "example.com:8443"
    assert str(result["http_only"]).strip() == "False"


def test_create_behind_the_edge_with_skip_tls_is_plain_http(tmp_path):
    result = _create_domain(tmp_path, {"HTTP_PORT": "8080"},
                            skip_tls=True)
    assert result["domain"].strip() == "example.com:8080"
    assert str(result["http_only"]).strip() == "True"


@pytest.mark.parametrize("domain", ["localhost", "wiki.localhost", "192.0.2.4"])
def test_create_behind_the_edge_accepts_unnamed_hosts(tmp_path, domain):
    assert _create_domain(tmp_path, {}, domain)["domain"].strip() == domain


# --- add ---------------------------------------------------------------------- #

def test_add_behind_the_edge_keeps_the_port(tmp_path):
    inst = _instance(tmp_path, {"HTTPS_PORT": "8443"})
    out = tmp_path / "out.json"
    tasks = _tasks(ADD, "Read the instance's ports from .env",
                   "Parse URL into domain and path") + [{
        "ansible.builtin.copy": {"content": "{{ _add_domain | trim }}",
                                 "dest": str(out), "mode": "0644"}}]
    _play(tmp_path, tasks, {"instance_path": str(inst), "url": "docs.example.com"})
    assert out.read_text() == "docs.example.com:8443"


# --- probes and doctor -------------------------------------------------------- #

def test_the_wiki_probe_uses_the_port_the_edge_serves(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("HTTPS_PORT=8443\n")
    seen = {}
    monkeypatch.setattr(_helpers, "_fetch_is_mediawiki",
                        lambda url, loopback_port=None: seen.setdefault("port", loopback_port))
    _helpers._probe_wiki_local("https://example.com/w/api.php", str(tmp_path))
    assert seen["port"] == 8443


def test_doctor_names_the_edge_ca_for_an_internal_certificate():
    now = datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc)
    entries = [("example.com", "CN=Canasta edge CA", "Jan 19 00:00:00 2029 GMT",
                "CN=example.com")]
    out = "\n".join(doctor._origin_tls_lines_from_entries(
        "one", entries, now, tls_internal=True, edge=True))
    assert "OK (the edge's internal CA — TLS_MODE=internal" in out


def test_doctor_warns_when_the_edge_has_no_public_certificate():
    now = datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc)
    entries = [("example.com", "CN=Canasta edge CA", "Jan 19 00:00:00 2029 GMT",
                "CN=example.com")]
    out = "\n".join(doctor._origin_tls_lines_from_entries(
        "one", entries, now, tls_internal=False, edge=True))
    assert "WARN — served by the edge's internal CA" in out
    assert "Caddy" not in out
