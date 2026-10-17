"""Ports and URLs for instances behind the HAProxy edge.

Behind the edge every wiki is served at https://<host> on 443, so nothing
may put a port on its URLs, and the instance has no ports or TLS switch of
its own to set. The edge, for its part, owns 80 and 443. The real task files
run here against temp instances.
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


# --- config set CANASTA_ENABLE_EDGE -------------------------------------------- #

def _side_effect(tmp_path, inst, value):
    return _play(tmp_path, [{"ansible.builtin.include_tasks":
                             os.path.join(CONFIG_TASKS, "_side_effects.yml")}],
                 {"instance_path": str(inst), "_config_key": "CANASTA_ENABLE_EDGE",
                  "_config_value": value})


def test_joining_the_edge_drops_the_port(tmp_path):
    inst = _instance(tmp_path, {"HTTPS_PORT": "8443"}, url="example.com:8443/docs")
    _side_effect(tmp_path, inst, "true")
    assert _url(inst) == "example.com/docs"
    assert _env(inst)["MW_SITE_SERVER"] == "https://example.com"
    assert _env(inst)["MW_SITE_FQDN"] == "example.com"


def test_joining_the_edge_serves_https_even_if_caddy_was_http_only(tmp_path):
    inst = _instance(tmp_path, {"CADDY_AUTO_HTTPS": "off", "HTTP_PORT": "8080"},
                     url="example.com:8080")
    _side_effect(tmp_path, inst, "true")
    assert _url(inst) == "example.com"
    assert _env(inst)["MW_SITE_SERVER"] == "https://example.com"


def test_leaving_the_edge_restores_the_instance_port(tmp_path):
    inst = _instance(tmp_path, {"HTTPS_PORT": "8443"})
    _side_effect(tmp_path, inst, "false")
    assert _url(inst) == "example.com:8443"
    assert _env(inst)["MW_SITE_SERVER"] == "https://example.com:8443"


def test_a_non_boolean_value_is_refused(tmp_path):
    inst = _instance(tmp_path, {})
    out = _play(tmp_path, [{"ansible.builtin.include_tasks":
                            os.path.join(CONFIG_TASKS, "_side_effects.yml")}],
                {"instance_path": str(inst), "_config_key": "CANASTA_ENABLE_EDGE",
                 "_config_value": "yes"}, expect_fail=True)
    assert "must be 'true' or 'false'" in out


@pytest.mark.parametrize("key", ["HTTP_PORT", "HTTPS_PORT", "CADDY_AUTO_HTTPS"])
def test_port_and_tls_keys_are_refused_behind_the_edge(tmp_path, key):
    inst = _instance(tmp_path, {"CANASTA_ENABLE_EDGE": "true"})
    tasks = _tasks(os.path.join(CONFIG_TASKS, "_validate_key.yml"),
                   "Read whether the instance is behind the edge",
                   "Block port and TLS keys behind the edge")
    out = _play(tmp_path, tasks, {"instance_path": str(inst), "_config_key": key},
                expect_fail=True)
    assert "cannot be set on an instance behind the HAProxy" in out


def test_port_keys_are_allowed_without_the_edge(tmp_path):
    inst = _instance(tmp_path, {})
    tasks = _tasks(os.path.join(CONFIG_TASKS, "_validate_key.yml"),
                   "Read whether the instance is behind the edge",
                   "Block port and TLS keys behind the edge")
    _play(tmp_path, tasks, {"instance_path": str(inst), "_config_key": "HTTP_PORT"})


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


def test_create_behind_the_edge_puts_no_port_on_the_url(tmp_path):
    result = _create_domain(tmp_path, {"CANASTA_ENABLE_EDGE": "true",
                                       "HTTPS_PORT": "8443"})
    assert result["domain"].strip() == "example.com"
    assert str(result["http_only"]).strip() == "False"


def test_create_without_the_edge_keeps_the_port(tmp_path):
    result = _create_domain(tmp_path, {"HTTPS_PORT": "8443"})
    assert result["domain"].strip() == "example.com:8443"


@pytest.mark.parametrize("domain", ["localhost", "wiki.localhost", "192.0.2.4"])
def test_create_behind_the_edge_refuses_names_it_cannot_route(tmp_path, domain):
    inst = _instance(tmp_path, {"CANASTA_ENABLE_EDGE": "true"})
    out = _play(tmp_path, [{"ansible.builtin.include_tasks": ENVFILE}],
                {"instance_path": str(inst), "domain_name": domain}, expect_fail=True)
    assert "routes each wiki by its hostname" in out


def test_create_behind_the_edge_refuses_skip_tls(tmp_path):
    inst = _instance(tmp_path, {"CANASTA_ENABLE_EDGE": "true"})
    out = _play(tmp_path, [{"ansible.builtin.include_tasks": ENVFILE}],
                {"instance_path": str(inst), "domain_name": "example.com",
                 "skip_tls": True}, expect_fail=True)
    assert "--skip-tls cannot be combined" in out


# --- add ---------------------------------------------------------------------- #

def test_add_behind_the_edge_puts_no_port_on_the_url(tmp_path):
    inst = _instance(tmp_path, {"CANASTA_ENABLE_EDGE": "true", "HTTPS_PORT": "8443"})
    out = tmp_path / "out.json"
    tasks = _tasks(ADD, "Read the instance's ports from .env",
                   "Parse URL into domain and path") + [{
        "ansible.builtin.copy": {"content": "{{ _add_domain | trim }}",
                                 "dest": str(out), "mode": "0644"}}]
    _play(tmp_path, tasks, {"instance_path": str(inst), "url": "docs.example.com"})
    assert out.read_text() == "docs.example.com"


# --- who owns 80 and 443 ------------------------------------------------------ #

def _refusal(tmp_path, publish):
    tasks = _tasks(os.path.join(ORCH_TASKS, "edge_sync.yml"), "Refuse ports the edge owns")
    return _play(tmp_path, tasks, {
        "instance_id": "plain",
        "_edge": {"members": ["one", "two"], "running": True},
        "_edge_instance_env": {"variables": publish},
    }, expect_fail=bool(set(publish.values()) & {"80", "443"}))


def test_an_instance_outside_the_edge_cannot_publish_its_ports(tmp_path):
    out = _refusal(tmp_path, {"CANASTA_CADDY_HTTP_PUBLISH": "80",
                              "CANASTA_CADDY_HTTPS_PUBLISH": "443"})
    assert "publishes port 80 and 443" in out and "one, two" in out


def test_an_instance_outside_the_edge_may_use_other_ports(tmp_path):
    _refusal(tmp_path, {"CANASTA_CADDY_HTTP_PUBLISH": "8080",
                        "CANASTA_CADDY_HTTPS_PUBLISH": "8443"})


# --- probes and doctor -------------------------------------------------------- #

def test_the_wiki_probe_goes_through_the_edge(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("CANASTA_ENABLE_EDGE=true\nHTTPS_PORT=8443\n")
    seen = {}
    monkeypatch.setattr(_helpers, "_fetch_is_mediawiki",
                        lambda url, loopback_port=None: seen.setdefault("port", loopback_port))
    _helpers._probe_wiki_local("https://example.com/w/api.php", str(tmp_path))
    assert seen["port"] == 443


def test_doctor_names_the_edge_ca_for_an_internal_certificate():
    now = datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc)
    entries = [("example.com", "CN=Canasta edge CA", "Jan 19 00:00:00 2029 GMT",
                "CN=example.com")]
    out = "\n".join(doctor._origin_tls_lines_from_entries(
        "one", entries, now, tls_internal=True, edge=True))
    assert "OK (the edge's internal CA — CADDY_TLS_MODE=internal" in out


def test_doctor_warns_when_the_edge_has_no_public_certificate():
    now = datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc)
    entries = [("example.com", "CN=Canasta edge CA", "Jan 19 00:00:00 2029 GMT",
                "CN=example.com")]
    out = "\n".join(doctor._origin_tls_lines_from_entries(
        "one", entries, now, tls_internal=False, edge=True))
    assert "WARN — served by the edge's internal CA" in out
    assert "Caddy" not in out
