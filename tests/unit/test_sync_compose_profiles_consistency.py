"""The Compose-profile reconciliation is implemented twice — in Python
(direct_commands/_helpers.py, used by the start/stop/rebuild fast path and the
doctor consistency check) and in Ansible
(roles/orchestrator/tasks/sync_compose_profiles.yml, used by gitops pull and
config set). Both read their tables from vars/compose_profiles.yml and
vars/images.yml, and must derive identical COMPOSE_PROFILES and
CANASTA_CADDY_IMAGE.
"""

import os
import re
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers  # noqa: E402

SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "sync_compose_profiles.yml")
VARS = os.path.join(REPO_ROOT, "vars")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _text():
    with open(SYNC) as f:
        return f.read()


def _vars(name):
    with open(os.path.join(VARS, name)) as f:
        return yaml.safe_load(f)


def _ansible_trusted_proxy_modes():
    m = re.search(r"_tp_mode in \[([^\]]+)\]", _text())
    return {s.strip().strip("'\"") for s in m.group(1).split(",")} if m else set()


def _run_sync(tmp_path, env_text):
    """Run sync_compose_profiles.yml against an .env; return the new .env."""
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(env_text)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(VARS, "compose_profiles.yml"),
                       os.path.join(VARS, "images.yml")],
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": SYNC}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return {k: v for k, v, c in _helpers._parse_env_entries(
        (inst / ".env").read_text()) if not c and k}


class TestEmptyMeansUnset:
    """A key present with an empty value is a host storing no opinion, not
    "off". Jinja's single-argument default() fires only on *undefined*, so
    `CANASTA_ENABLE_VARNISH=` read as false, dropped varnish from
    COMPOSE_PROFILES, and left its container running unmanaged — a 503 on
    every request once web was recreated. Only varnish defaults to true,
    so it is the only flag whose empty value flips behavior; the rest are
    pinned here so a future true-defaulting flag cannot inherit the bug.
    """

    def test_every_ansible_flag_read_treats_empty_as_unset(self):
        bare = re.findall(
            r"CANASTA_ENABLE_\w+\s*\|\s*default\('(?:true|false)'\)",
            _text())
        assert not bare, (
            "single-argument default() only fires on undefined, so an empty "
            "value falls through as-is: %s" % bare)
        assert "default(item.default, true)" in _text()

    def test_python_reader_treats_empty_as_unset(self):
        for flag, default in (("CANASTA_ENABLE_VARNISH", "true"),
                              ("CANASTA_ENABLE_CROWDSEC", "false")):
            blank = _helpers._reconcile_compose_profiles({flag: ""}, [])
            unset = _helpers._reconcile_compose_profiles({}, [])
            assert blank == unset, (
                "%s= must behave as unset (default %s), not as off"
                % (flag, default))

    def test_blank_varnish_flag_keeps_the_profile(self):
        desired = _helpers._reconcile_compose_profiles(
            {"CANASTA_ENABLE_VARNISH": ""}, ["varnish", "internal-db"])
        assert "varnish" in desired, (
            "dropping varnish here orphans a running container that compose "
            "then refuses to manage")

    def test_an_explicit_false_still_disables(self):
        desired = _helpers._reconcile_compose_profiles(
            {"CANASTA_ENABLE_VARNISH": "false"}, ["varnish"])
        assert "varnish" not in desired, (
            "an explicit false is an opinion and must still turn it off")

    def test_blank_external_db_still_means_internal(self):
        desired = _helpers._reconcile_compose_profiles(
            {"USE_EXTERNAL_DB": ""}, [])
        assert "internal-db" in desired


class TestSyncComposeProfilesConsistency:
    def test_python_tables_come_from_the_vars_files(self):
        profiles = _vars("compose_profiles.yml")
        assert _helpers._MANAGED_PROFILES == [
            (p["profile"], p["flag"], p["default"])
            for p in profiles["canasta_managed_profiles"]]
        assert (_helpers._MANAGED_PROFILE_SERVICES
                == profiles["canasta_managed_profile_services"])
        assert (_helpers._CADDY_PLUGIN_IMAGE
                == _vars("images.yml")["canasta_caddy_plugin_image"])

    def test_ansible_reads_the_tables_instead_of_literals(self):
        text = _text()
        assert "canasta_managed_profiles" in text
        assert "_profile_services: \"{{ canasta_managed_profile_services }}\"" in text
        assert "canasta_caddy_plugin_image" in text
        assert "canasta-caddy" not in text
        assert "ternary(['observable']" not in text

    def test_trusted_proxy_modes_match(self):
        assert _ansible_trusted_proxy_modes() == set(
            _helpers._CADDY_PLUGIN_TRUSTED_PROXY_MODES), (
            "the trusted-proxy plugin modes have drifted")


@pytest.mark.parametrize("env_text", [
    "",
    "COMPOSE_PROFILES=custom\n",
    "CANASTA_ENABLE_VARNISH=\nCANASTA_ENABLE_ELASTICSEARCH=true\n",
    "CANASTA_ENABLE_VARNISH=false\nCANASTA_ENABLE_OBSERVABILITY=TRUE\n"
    "USE_EXTERNAL_DB=true\n",
    "CADDY_TRUSTED_PROXIES=cloudflare\n",
    "CANASTA_CADDY_IMAGE=ghcr.io/canastawiki/canasta-caddy:1.0\n",
    "CADDY_TRUSTED_PROXIES=imperva\nCANASTA_CADDY_IMAGE=example.com/caddy:1\n",
])
def test_ansible_and_python_derive_the_same_env(tmp_path, env_text):
    env_text = "MW_SITE_SERVER=https://example.com\n" + env_text
    ansible = _run_sync(tmp_path, env_text)

    py_dir = tmp_path / "py"
    py_dir.mkdir()
    (py_dir / ".env").write_text(env_text)
    _helpers._sync_compose_profiles({"path": str(py_dir), "host": "localhost"})
    python = {k: v for k, v, c in _helpers._parse_env_entries(
        (py_dir / ".env").read_text()) if not c and k}

    assert ansible.get("COMPOSE_PROFILES") == python.get("COMPOSE_PROFILES")
    assert (ansible.get("CANASTA_CADDY_IMAGE", "")
            == python.get("CANASTA_CADDY_IMAGE", ""))
