"""Behind the HAProxy edge, Caddy serves plain HTTP and trusts the edge.

The edge terminates TLS and forwards HTTP, as the ingress does on Kubernetes.
A Caddy that still redirected to HTTPS would loop, and one that did not trust
the edge's address would attribute every request to the edge. The real
rewrite_caddy.yml runs here against a temp instance.
"""

import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
REWRITE = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml")


def _caddyfile(tmp_path, env_lines, expect_fail=False, global_text=None):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    if global_text is not None:
        (inst / "config" / "Caddyfile.global").write_text(global_text)
    (inst / ".env").write_text("".join(line + "\n" for line in env_lines))
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump(
        {"wikis": [{"id": "main", "url": "example.com", "name": "Main"}]}))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, instance_path=str(inst),
                     instance_orchestrator="compose", canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable),
        "tasks": [{"ansible.builtin.include_tasks": REWRITE}],
    }]))
    empty = tmp_path / "collections"
    empty.mkdir()
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
    if expect_fail:
        assert proc.returncode != 0, proc.stdout
        return proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (inst / "config" / "Caddyfile").read_text()


def test_caddy_is_the_tls_edge_by_default(tmp_path):
    out = _caddyfile(tmp_path, [])
    assert "\nexample.com {" in out
    assert "trusted_proxies" not in out


@pytest.mark.parametrize("flag", ["true", "TRUE"])
def test_behind_the_edge_caddy_serves_http_only(tmp_path, flag):
    out = _caddyfile(tmp_path, ["CANASTA_ENABLE_EDGE=%s" % flag,
                                "CADDY_AUTO_HTTPS=on", "CADDY_TLS_MODE=internal"])
    assert "\nhttp://example.com {" in out
    assert "tls internal" not in out


def test_behind_the_edge_crowdsec_sees_the_client_address(tmp_path):
    out = _caddyfile(tmp_path, ["CANASTA_ENABLE_EDGE=true",
                                "CANASTA_ENABLE_CROWDSEC=true"])
    assert "trusted_proxies static 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16" in out


def test_behind_the_edge_a_cdn_mode_combines_the_edge_and_cdn_ranges(tmp_path):
    out = _caddyfile(tmp_path, ["CANASTA_ENABLE_EDGE=true",
                                "CADDY_TRUSTED_PROXIES=cloudflare"])
    assert "trusted_proxies combine {" in out
    assert "static 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16" in out
    assert "provider cloudflare" in out


def test_without_the_edge_a_cdn_mode_stands_alone(tmp_path):
    out = _caddyfile(tmp_path, ["CADDY_TRUSTED_PROXIES=cloudflare"])
    assert "trusted_proxies cdn_ranges {" in out
    assert "combine" not in out


def test_acme_dns_is_refused_without_the_edge(tmp_path):
    out = _caddyfile(tmp_path, ["CADDY_TLS_MODE=acme-dns"], expect_fail=True)
    assert "needs CANASTA_ENABLE_EDGE=true" in out


def test_acme_dns_behind_the_edge_renders(tmp_path):
    out = _caddyfile(tmp_path, ["CANASTA_ENABLE_EDGE=true", "CADDY_TLS_MODE=acme-dns"])
    assert "\nhttp://example.com {" in out


WWW_REDIRECT = "www.example.com {\n    redir https://example.com{uri} 301\n}\n"


def test_behind_the_edge_user_sites_are_served_over_http(tmp_path):
    out = _caddyfile(tmp_path, ["CANASTA_ENABLE_EDGE=true"], global_text=WWW_REDIRECT)
    assert "\nhttp://www.example.com {" in out


def test_without_the_edge_user_sites_are_left_alone(tmp_path):
    out = _caddyfile(tmp_path, [], global_text=WWW_REDIRECT)
    assert "\nwww.example.com {" in out
