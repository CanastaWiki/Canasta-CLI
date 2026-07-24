"""Enforcement guard: the Compose-profile reconciliation is implemented twice —
in Python (direct_commands/_helpers.py, used by the start/stop/rebuild fast-path
and the doctor consistency check) and in Ansible
(roles/orchestrator/tasks/sync_compose_profiles.yml, used by gitops pull and
config set). They must derive identical COMPOSE_PROFILES.

This test extracts the managed-profile data from the Ansible file and fails if
it diverges from the Python constants — the managed-profile list, the
flag->profile mapping and its defaults, the internal-db (inverse-of-external-DB)
rule, the profile->services teardown map, the Caddy plugin image, and the
trusted-proxy modes. So the two implementations cannot silently drift.
"""

import os
import re

import yaml

from direct_commands import _helpers

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "sync_compose_profiles.yml")


def _text():
    with open(SYNC) as f:
        return f.read()


def _ansible_managed_list():
    m = re.search(r"reject\('in',\s*\[([^\]]+)\]\)", _text())
    return {s.strip().strip("'\"") for s in m.group(1).split(",")} if m else set()


def _ansible_flag_map():
    # (profile, flag, default) for each CANASTA_ENABLE_* ternary line.
    return {
        (profile, flag, default)
        for flag, default, profile in re.findall(
            r"variables\.(\w+)\s*\|\s*default\('(\w+)'\)\s*\|\s*"
            r"lower == 'true'\)\s*\|\s*ternary\(\['([^']+)'\]", _text())
    }


def _ansible_profile_services():
    for t in yaml.safe_load(_text()) or []:
        v = (t.get("vars") or {}) if isinstance(t, dict) else {}
        if "_profile_services" in v:
            return {k: list(val) for k, val in v["_profile_services"].items()}
    return {}


def _ansible_caddy_image():
    m = re.search(r"_plugin_caddy_image:\s*\"([^\"]+)\"", _text())
    return m.group(1) if m else None


def _ansible_trusted_proxy_modes():
    m = re.search(r"_tp_mode in \[([^\]]+)\]", _text())
    return {s.strip().strip("'\"") for s in m.group(1).split(",")} if m else set()


class TestSyncComposeProfilesConsistency:
    def test_flag_to_profile_mapping_matches(self):
        ansible = _ansible_flag_map()
        assert ansible, "could not extract the flag->profile mapping from Ansible"
        assert ansible == set(_helpers._MANAGED_PROFILES), (
            "the CANASTA_ENABLE_* -> profile mapping (and defaults) has drifted "
            "between Python _MANAGED_PROFILES and sync_compose_profiles.yml:\n"
            "  Ansible: %s\n  Python:  %s"
            % (sorted(ansible), sorted(set(_helpers._MANAGED_PROFILES))))

    def test_managed_profile_names_match(self):
        ansible = _ansible_managed_list()
        assert ansible, "could not extract the managed-profile list from Ansible"
        assert ansible == set(_helpers._MANAGED_PROFILE_NAMES), (
            "the managed-profile list has drifted:\n  Ansible: %s\n  Python:  %s"
            % (sorted(ansible), sorted(_helpers._MANAGED_PROFILE_NAMES)))

    def test_internal_db_inverse_rule_present_in_both(self):
        assert re.search(
            r"variables\.USE_EXTERNAL_DB\s*\|\s*default\('false'\)\s*\|\s*"
            r"lower != 'true'\)\s*\|\s*ternary\(\['internal-db'\]", _text()), (
            "Ansible must derive internal-db from NOT(USE_EXTERNAL_DB)")
        assert "internal-db" in _helpers._MANAGED_PROFILE_NAMES

    def test_profile_services_map_matches(self):
        ansible = _ansible_profile_services()
        assert ansible, "could not extract _profile_services from Ansible"
        assert ansible == _helpers._MANAGED_PROFILE_SERVICES, (
            "the profile->services teardown map has drifted:\n"
            "  Ansible: %s\n  Python:  %s"
            % (ansible, _helpers._MANAGED_PROFILE_SERVICES))

    def test_caddy_plugin_image_matches(self):
        assert _ansible_caddy_image() == _helpers._CADDY_PLUGIN_IMAGE, (
            "the Caddy plugin image literal has drifted between "
            "sync_compose_profiles.yml and _helpers.py")

    def test_trusted_proxy_modes_match(self):
        assert _ansible_trusted_proxy_modes() == set(
            _helpers._CADDY_PLUGIN_TRUSTED_PROXY_MODES), (
            "the trusted-proxy plugin modes have drifted")
