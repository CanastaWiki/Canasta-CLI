"""Tests for `canasta doctor`'s Kubernetes config-drift check — detecting
local edits to managed config files that a `canasta reconcile` would apply.

Scope guards live here too: secret keys and extensions/skins must NOT be
flagged, and gitops-managed instances are pointed at `gitops status` instead.
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from direct_commands import doctor  # noqa: E402

DELIM = doctor._helpers._SENTINEL


def _snapshot(web=None, caddy=None, varnish=None, crowdsec=None):
    """Serialize a configData snapshot the way values-configdata.yaml stores
    it (what the drift check reads as its baseline)."""
    return yaml.safe_dump({"configData": {
        "web": web or {},
        "caddy": caddy or {},
        "varnish": varnish or {},
        "crowdsec": crowdsec or {},
    }})


def _stdout(is_gitops, baseline_yaml, files):
    """Reproduce the drift script's stdout framing for parser tests."""
    out = ("GITOPS_YES\n" if is_gitops else "GITOPS_NO\n")
    out += DELIM + "\n" + baseline_yaml
    for relpath, content in files.items():
        out += DELIM + "FILE:" + relpath + "\n" + content
    out += DELIM + "END\n"
    return out


# --- key encoding / secret stripping ---------------------------------------

class TestEncoding:
    def test_settings_key_matches_sync_encoding(self):
        assert (doctor._k8s_settings_key("config/settings/global/Foo.php")
                == "settings--global--Foo.php")

    def test_settings_key_top_level_file(self):
        assert (doctor._k8s_settings_key("config/settings/Bar.php")
                == "settings--Bar.php")

    def test_display_path_round_trips_settings(self):
        assert (doctor._k8s_display_path("web", "settings--global--Foo.php")
                == "config/settings/global/Foo.php")

    def test_strip_env_secrets_drops_only_secret_keys(self):
        text = ("PHP_UPLOAD_MAX_FILESIZE=100M\n"
                "MYSQL_PASSWORD=hunter2\n"
                "MW_SECRET_KEY=abc\n"
                "CANASTA_ENABLE_VARNISH=true\n")
        assert doctor._k8s_strip_env_secrets(text) == [
            "PHP_UPLOAD_MAX_FILESIZE=100M",
            "CANASTA_ENABLE_VARNISH=true",
        ]


# --- drift comparison -------------------------------------------------------

class TestDrift:
    def test_no_drift_when_files_match(self):
        base = {"web": {"wikis.yaml": "a: 1\n"}, "caddy": {}, "varnish": {},
                "crowdsec": {}}
        cur = doctor._k8s_current_channels({"config/wikis.yaml": "a: 1\n"})
        assert doctor._k8s_config_drift(base, cur) == []

    def test_trailing_newline_is_not_drift(self):
        base = {"web": {"wikis.yaml": "a: 1\n"}}
        cur = doctor._k8s_current_channels({"config/wikis.yaml": "a: 1"})
        assert doctor._k8s_config_drift(base, cur) == []

    def test_changed_settings_file_flagged(self):
        base = {"web": {"settings--global--Foo.php": "<?php $x = 1;\n"}}
        cur = doctor._k8s_current_channels(
            {"config/settings/global/Foo.php": "<?php $x = 2;\n"})
        assert doctor._k8s_config_drift(base, cur) == [
            ("config/settings/global/Foo.php", "changed")]

    def test_added_file_flagged(self):
        base = {"web": {}}
        cur = doctor._k8s_current_channels(
            {"config/settings/global/New.php": "<?php\n"})
        assert doctor._k8s_config_drift(base, cur) == [
            ("config/settings/global/New.php", "added")]

    def test_removed_file_flagged(self):
        base = {"web": {"settings--global--Gone.php": "<?php\n"}}
        cur = doctor._k8s_current_channels({})
        assert doctor._k8s_config_drift(base, cur) == [
            ("config/settings/global/Gone.php", "removed")]

    def test_secret_only_env_change_is_not_drift(self):
        # Baseline stores the secret-stripped .env; changing MYSQL_PASSWORD
        # locally must not be reported (it never reaches the ConfigMap).
        base = {"web": {".env": "PHP_UPLOAD_MAX_FILESIZE=100M\n"}}
        cur = doctor._k8s_current_channels(
            {".env": "PHP_UPLOAD_MAX_FILESIZE=100M\nMYSQL_PASSWORD=new\n"})
        assert doctor._k8s_config_drift(base, cur) == []

    def test_non_secret_env_change_is_drift(self):
        base = {"web": {".env": "PHP_UPLOAD_MAX_FILESIZE=100M\n"}}
        cur = doctor._k8s_current_channels(
            {".env": "PHP_UPLOAD_MAX_FILESIZE=200M\n"})
        assert doctor._k8s_config_drift(base, cur) == [(".env", "changed")]


# --- payload parsing --------------------------------------------------------

class TestParse:
    def test_parses_gitops_baseline_and_files(self):
        baseline = _snapshot(web={"wikis.yaml": "a: 1\n"})
        stdout = _stdout(False, baseline, {
            ".env": "K=V\n",
            "config/settings/global/Foo.php": "<?php\n",
        })
        is_gitops, base_text, files = doctor._parse_k8s_drift_payload(stdout)
        assert is_gitops is False
        assert yaml.safe_load(base_text)["configData"]["web"] == {
            "wikis.yaml": "a: 1\n"}
        assert files == {
            ".env": "K=V\n",
            "config/settings/global/Foo.php": "<?php\n",
        }

    def test_detects_gitops_marker(self):
        stdout = _stdout(True, _snapshot(), {})
        is_gitops, _, _ = doctor._parse_k8s_drift_payload(stdout)
        assert is_gitops is True


# --- end-to-end line building ----------------------------------------------

class TestLines:
    def test_gitops_points_to_gitops_status(self):
        lines = doctor._k8s_drift_lines_from_payload("w", True, "", {})
        joined = " ".join(lines)
        assert "gitops status" in joined
        assert "reconcile" not in joined

    def test_no_baseline_notes_reconcile_needed(self):
        lines = doctor._k8s_drift_lines_from_payload("w", False, "", {})
        assert any("No deployed config snapshot" in ln for ln in lines)

    def test_clean_instance_reports_ok(self):
        baseline = _snapshot(web={"wikis.yaml": "a: 1\n"})
        lines = doctor._k8s_drift_lines_from_payload(
            "w", False, baseline, {"config/wikis.yaml": "a: 1\n"})
        assert any("OK (deployed config matches" in ln for ln in lines)

    def test_drift_warns_and_recommends_reconcile(self):
        baseline = _snapshot(web={"settings--global--Foo.php": "<?php 1;\n"})
        lines = doctor._k8s_drift_lines_from_payload(
            "w", False, baseline,
            {"config/settings/global/Foo.php": "<?php 2;\n"})
        joined = " ".join(lines)
        assert "WARN" in joined
        assert "config/settings/global/Foo.php (changed)" in joined
        assert "canasta reconcile" in joined
