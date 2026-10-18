"""The TLS keys lost their CADDY_ prefix, and CADDY_AUTO_HTTPS=off became
TLS_MODE=off.

A .env written before the rename must read as if renamed everywhere: the
edge decides an instance's certificates before the upgrade migration
rewrites the file, and a host the upgrade refuses keeps the old names. A
gitops env.template may keep the old names for good, so the render writes
the new ones and settings are stored under the template's.
"""

import os
import subprocess
import sys

import pytest
import yaml

import canasta_edge
import canasta_env
import canasta_env_keys
from mock_ansible import run_module_with_params

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "filter_plugins"))
sys.path.insert(0, ROOT)

import canasta_gitops  # noqa: E402
from direct_commands import _helpers  # noqa: E402

OLD_ENV = (
    "MW_SITE_SERVER=https://example.com\n"
    "CADDY_TLS_MODE=internal\n"
    "CADDY_TRUSTED_PROXIES=cloudflare\n"
    "CADDY_TLS_CERT=origin.crt\n"
)


@pytest.mark.parametrize("values,expected", [
    ({"CADDY_TLS_MODE": "internal"}, "internal"),
    ({"CADDY_TLS_MODE": "internal", "CADDY_AUTO_HTTPS": "off"}, "off"),
    ({"CADDY_TLS_MODE": "internal", "CADDY_AUTO_HTTPS": "on"}, "internal"),
    ({"CADDY_AUTO_HTTPS": "off"}, "off"),
    ({"CADDY_TLS_MODE": "internal", "TLS_MODE": "custom"}, "custom"),
    ({"CADDY_AUTO_HTTPS": "off", "TLS_MODE": "acme"}, "acme"),
    ({}, None),
])
def test_values_read_under_the_new_names(values, expected):
    assert canasta_env_keys.modernize_values(values).get("TLS_MODE") == expected


def test_text_is_rewritten_in_place():
    text = ("A=1\nCADDY_TLS_MODE=internal\nCADDY_AUTO_HTTPS=off\n"
            "CADDY_TRUSTED_PROXIES=10.0.0.0/8\n# CADDY_TLS_MODE=custom\n")
    assert canasta_env_keys.modernize_text(text) == (
        "A=1\nTLS_MODE=off\nTRUSTED_PROXIES=10.0.0.0/8\n# CADDY_TLS_MODE=custom\n")


def test_edge_reads_an_unmigrated_env(tmp_path):
    # The upgrade plans the edge move before its migrations run: an
    # instance on internal certificates must not be read as acme.
    (tmp_path / ".env").write_text(OLD_ENV)
    env = canasta_edge.read_env(str(tmp_path))
    assert env["TLS_MODE"] == "internal"
    assert env["TRUSTED_PROXIES"] == "cloudflare"
    assert env["TLS_CERT"] == "origin.crt"


def test_canasta_env_reads_an_unmigrated_env(tmp_path):
    path = tmp_path / ".env"
    path.write_text(OLD_ENV + "CADDY_AUTO_HTTPS=off\n")
    result, failed, _ = run_module_with_params(canasta_env, {
        "path": str(path), "state": "read", "key": "TLS_MODE",
        "value": None, "keys": None})
    assert not failed and result["value"] == "off"
    result, failed, _ = run_module_with_params(canasta_env, {
        "path": str(path), "state": "read_all", "key": None,
        "value": None, "keys": None})
    assert result["variables"]["TRUSTED_PROXIES"] == "cloudflare"


def test_setting_a_new_key_drops_its_old_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text(OLD_ENV + "CADDY_AUTO_HTTPS=off\n")
    result, failed, _ = run_module_with_params(canasta_env, {
        "path": str(path), "state": "set", "key": "TLS_MODE",
        "value": "acme-dns", "keys": None})
    assert not failed and result["changed"]
    text = path.read_text()
    assert "TLS_MODE=acme-dns" in text
    assert "CADDY_TLS_MODE" not in text and "CADDY_AUTO_HTTPS" not in text
    assert "CADDY_TRUSTED_PROXIES=cloudflare" in text


def test_unsetting_a_new_key_drops_its_old_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text(OLD_ENV)
    result, failed, _ = run_module_with_params(canasta_env, {
        "path": str(path), "state": "unset", "key": "TRUSTED_PROXIES",
        "value": None, "keys": None})
    assert not failed and result["changed"]
    assert "TRUSTED_PROXIES" not in path.read_text()


def test_fast_path_reads_an_unmigrated_env(tmp_path):
    (tmp_path / ".env").write_text(OLD_ENV + "CADDY_AUTO_HTTPS=off\n")
    inst = {"path": str(tmp_path), "host": "localhost"}
    assert _helpers._read_env_for(inst, "TLS_MODE") == "off"
    assert _helpers._read_env_for(inst, "TRUSTED_PROXIES") == "cloudflare"
    assert _helpers._read_env_file(str(tmp_path), "localhost")["TLS_MODE"] == "off"


LEGACY_TEMPLATE = (
    "MW_SITE_SERVER={{mw_site_server}}\n"
    "CADDY_AUTO_HTTPS={{caddy_auto_https}}\n"
    "CADDY_TLS_MODE={{caddy_tls_mode}}\n"
    "CADDY_TRUSTED_PROXIES={{caddy_trusted_proxies}}\n"
)


def test_a_legacy_template_renders_the_new_names():
    out = canasta_gitops.canasta_env_render(LEGACY_TEMPLATE, {
        "mw_site_server": "https://example.com", "caddy_auto_https": "on",
        "caddy_tls_mode": "internal", "caddy_trusted_proxies": "cloudflare"})
    assert "TLS_MODE=internal\n" in out and "TRUSTED_PROXIES=cloudflare\n" in out
    assert "CADDY_" not in out


@pytest.mark.parametrize("setting,stored", [
    ("TLS_MODE=off", ["CADDY_AUTO_HTTPS=off"]),
    ("TLS_MODE=acme-dns", ["CADDY_AUTO_HTTPS=on", "CADDY_TLS_MODE=acme-dns"]),
    ("TRUSTED_PROXIES=imperva", ["CADDY_TRUSTED_PROXIES=imperva"]),
    ("HTTP_PORT=8080", ["HTTP_PORT=8080"]),
])
def test_settings_are_stored_under_the_template_names(setting, stored):
    assert canasta_gitops.canasta_env_legacy_settings([setting], LEGACY_TEMPLATE) == stored


def test_settings_keep_the_new_names_in_a_new_template():
    template = "TLS_MODE={{tls_mode}}\n"
    assert canasta_gitops.canasta_env_legacy_settings(
        ["TLS_MODE=off", "TRUSTED_PROXIES=imperva"], template) == [
        "TLS_MODE=off", "TRUSTED_PROXIES=imperva"]


def test_unset_keys_are_found_under_the_template_names():
    assert canasta_gitops.canasta_env_legacy_keys(
        ["TLS_MODE", "TRUSTED_PROXIES"], LEGACY_TEMPLATE) == [
        "CADDY_TLS_MODE", "CADDY_AUTO_HTTPS", "CADDY_TRUSTED_PROXIES"]


def test_a_legacy_template_round_trips_through_the_sync():
    # .env as rendered from a legacy template agrees with its sources.
    host_vars = {"caddy_auto_https": "off", "caddy_tls_mode": "acme",
                 "mw_site_server": "http://example.com"}
    live = canasta_gitops.canasta_env_render(LEGACY_TEMPLATE, host_vars)
    plan = canasta_gitops.canasta_env_sync_plan(live, LEGACY_TEMPLATE, host_vars)
    assert plan == {"set": {}, "unset": [], "conflict": []}


def _playbook(tmp_path, tasks, play_vars):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": dict(play_vars, canasta_root=ROOT,
                     ansible_python_interpreter=sys.executable),
        "tasks": tasks,
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(ROOT, "roles", "common", "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(ROOT, "roles", "common", "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


def test_the_migration_renames_the_keys(tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(OLD_ENV + "CADDY_AUTO_HTTPS=on\n")
    proc = _playbook(tmp_path, [{"ansible.builtin.include_tasks": os.path.join(
        ROOT, "roles", "upgrade", "tasks", "migrations", "rename_tls_keys.yml")}],
        {"instance_path": str(inst), "instance_is_k8s": False})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (inst / ".env").read_text() == (
        "MW_SITE_SERVER=https://example.com\nTLS_MODE=internal\n"
        "TRUSTED_PROXIES=cloudflare\nTLS_CERT=origin.crt\n")
    assert oct((inst / ".env").stat().st_mode & 0o777) == "0o600"


def test_the_migration_drops_plain_http_on_kubernetes(tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text("CADDY_AUTO_HTTPS=off\nCADDY_TRUSTED_PROXIES=cloudflare\n")
    proc = _playbook(tmp_path, [{"ansible.builtin.include_tasks": os.path.join(
        ROOT, "roles", "upgrade", "tasks", "migrations", "rename_tls_keys.yml")}],
        {"instance_path": str(inst), "instance_is_k8s": True})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (inst / ".env").read_text() == "TRUSTED_PROXIES=cloudflare\n"


def test_upgrade_runs_the_migration():
    with open(os.path.join(ROOT, "roles", "upgrade", "tasks", "main.yml")) as f:
        assert "- rename_tls_keys.yml" in f.read()


@pytest.mark.parametrize("key,force", [
    ("CADDY_TLS_MODE", False), ("CADDY_AUTO_HTTPS", True), ("CADDY_TRUSTED_PROXIES", True),
])
def test_config_set_refuses_an_old_name(tmp_path, key, force):
    with open(os.path.join(ROOT, "roles", "config", "tasks", "_validate_key.yml")) as f:
        task = next(t for t in yaml.safe_load(f) if t.get("name") == "Refuse a renamed key")
    proc = _playbook(tmp_path, [task], {"_config_key": key, "force": force})
    assert proc.returncode != 0
    assert "was renamed to" in proc.stdout
