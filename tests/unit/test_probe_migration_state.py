"""Tests for roles/upgrade/files/probe_migration_state.py.

The probe runs on the target host and emits JSON consumed by the
upgrade migration tasks. The tests below build small instance-dir
fixtures under tmp_path and assert the JSON shape and content.
"""

import importlib.util
import json
import os

import pytest


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PROBE_PATH = os.path.join(
    REPO_ROOT, "roles", "upgrade", "files", "probe_migration_state.py",
)


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe_migration_state", PROBE_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def probe():
    return _load_probe()


@pytest.fixture
def instance_dir(tmp_path):
    """A skeleton instance directory the probe can chew on."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings").mkdir()
    return tmp_path


def _run(probe, base):
    """Drive the probe like Ansible would. Returns the parsed JSON."""
    import io
    import sys
    saved = sys.argv, sys.stdout
    sys.argv = ["probe_migration_state.py", str(base)]
    sys.stdout = io.StringIO()
    try:
        probe.main()
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.argv, sys.stdout = saved


# --- .env parsing ----------------------------------------------------


class TestEnvParsing:
    def test_missing_env_yields_empty_present_map(self, probe, instance_dir):
        result = _run(probe, instance_dir)
        assert result["env_present"] == {
            "MW_SECRET_KEY": False,
            "CANASTA_IMAGE": False,
            "COMPOSE_PROFILES": False,
            "USE_EXTERNAL_DB": False,
        }
        assert result["env"]["MW_SECRET_KEY"] == ""

    def test_present_keys_are_extracted(self, probe, instance_dir):
        (instance_dir / ".env").write_text(
            "HTTP_PORT=80\n"
            "MW_SECRET_KEY=abc123\n"
            "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.5.7\n"
        )
        result = _run(probe, instance_dir)
        assert result["env"]["MW_SECRET_KEY"] == "abc123"
        assert result["env"]["CANASTA_IMAGE"] == "ghcr.io/canastawiki/canasta:3.5.7"
        assert result["env_present"]["MW_SECRET_KEY"] is True
        assert result["env_present"]["COMPOSE_PROFILES"] is False

    def test_empty_value_is_present_but_empty(self, probe, instance_dir):
        """COMPOSE_PROFILES= (set to empty) is meaningful — external-DB
        instances intentionally have an empty profiles list. The probe
        must distinguish "present but empty" from "absent."""
        (instance_dir / ".env").write_text("COMPOSE_PROFILES=\n")
        result = _run(probe, instance_dir)
        assert result["env_present"]["COMPOSE_PROFILES"] is True
        assert result["env"]["COMPOSE_PROFILES"] == ""

    def test_quoted_values_are_unquoted(self, probe, instance_dir):
        (instance_dir / ".env").write_text(
            'MW_SECRET_KEY="abc123"\n'
            "CANASTA_IMAGE='ghcr.io/canastawiki/canasta:latest'\n"
        )
        result = _run(probe, instance_dir)
        assert result["env"]["MW_SECRET_KEY"] == "abc123"
        assert result["env"]["CANASTA_IMAGE"] == "ghcr.io/canastawiki/canasta:latest"

    def test_comments_and_blank_lines_skipped(self, probe, instance_dir):
        (instance_dir / ".env").write_text(
            "# a comment\n"
            "\n"
            "MW_SECRET_KEY=secret\n"
        )
        result = _run(probe, instance_dir)
        assert result["env"]["MW_SECRET_KEY"] == "secret"


# --- File probes ----------------------------------------------------


class TestFileProbes:
    def test_vector_php_absent(self, probe, instance_dir):
        result = _run(probe, instance_dir)
        assert result["files"]["vector_php"] is False
        assert result["files"]["vector_php_has_default_skin"] is False

    def test_vector_php_with_default_skin(self, probe, instance_dir):
        (instance_dir / "config" / "settings" / "global").mkdir()
        (instance_dir / "config" / "settings" / "global" / "Vector.php").write_text(
            "<?php\n$wgDefaultSkin = \"vector-2022\";\n"
        )
        result = _run(probe, instance_dir)
        assert result["files"]["vector_php"] is True
        assert result["files"]["vector_php_has_default_skin"] is True

    def test_vector_php_without_default_skin(self, probe, instance_dir):
        (instance_dir / "config" / "settings" / "global").mkdir()
        (instance_dir / "config" / "settings" / "global" / "Vector.php").write_text(
            "<?php\n// just a comment\n"
        )
        result = _run(probe, instance_dir)
        assert result["files"]["vector_php"] is True
        assert result["files"]["vector_php_has_default_skin"] is False

    def test_legacy_git_dir_detected(self, probe, instance_dir):
        (instance_dir / ".git").mkdir()
        result = _run(probe, instance_dir)
        assert result["files"]["legacy_git"] is True
        assert result["files"]["gitops_host"] is False

    def test_gitops_host_marker(self, probe, instance_dir):
        (instance_dir / ".gitops-host").write_text("primary\n")
        result = _run(probe, instance_dir)
        assert result["files"]["gitops_host"] is True
        assert result["files"]["legacy_git"] is False


class TestComposerLocal:
    def test_absent(self, probe, instance_dir):
        result = _run(probe, instance_dir)
        assert result["files"]["composer_local"] is False
        assert result["files"]["composer_local_empty_include"] is False

    def test_empty_include_array(self, probe, instance_dir):
        (instance_dir / "config" / "composer.local.json").write_text(
            json.dumps({"extra": {"merge-plugin": {"include": []}}})
        )
        result = _run(probe, instance_dir)
        assert result["files"]["composer_local"] is True
        assert result["files"]["composer_local_empty_include"] is True

    def test_populated_include_array(self, probe, instance_dir):
        (instance_dir / "config" / "composer.local.json").write_text(
            json.dumps({"extra": {"merge-plugin": {"include": ["foo/composer.json"]}}})
        )
        result = _run(probe, instance_dir)
        assert result["files"]["composer_local"] is True
        assert result["files"]["composer_local_empty_include"] is False

    def test_malformed_json_does_not_crash(self, probe, instance_dir):
        (instance_dir / "config" / "composer.local.json").write_text("{not json")
        result = _run(probe, instance_dir)
        assert result["files"]["composer_local"] is True
        assert result["files"]["composer_local_empty_include"] is False


class TestMyCnf:
    def test_absent(self, probe, instance_dir):
        result = _run(probe, instance_dir)
        assert result["files"]["mycnf"] is False
        assert result["files"]["mycnf_has_skip_binary_as_hex"] is False

    def test_present_clean(self, probe, instance_dir):
        (instance_dir / "my.cnf").write_text("[mysqld]\nlog-bin=mysql-bin\n")
        result = _run(probe, instance_dir)
        assert result["files"]["mycnf"] is True
        assert result["files"]["mycnf_has_skip_binary_as_hex"] is False

    def test_present_with_skip_binary_as_hex(self, probe, instance_dir):
        (instance_dir / "my.cnf").write_text(
            "[client]\nskip-binary-as-hex\n"
        )
        result = _run(probe, instance_dir)
        assert result["files"]["mycnf_has_skip_binary_as_hex"] is True


# --- Directory probes -----------------------------------------------


class TestOldWikiDirs:
    def test_only_system_dirs_yields_empty(self, probe, instance_dir):
        for sys_dir in ("settings", "logstash", "backup", "persistent"):
            (instance_dir / "config" / sys_dir).mkdir(exist_ok=True)
        result = _run(probe, instance_dir)
        assert result["old_wiki_dirs"] == []

    def test_per_wiki_dirs_listed(self, probe, instance_dir):
        for d in ("settings", "wiki1", "wiki2"):
            (instance_dir / "config" / d).mkdir(exist_ok=True)
        result = _run(probe, instance_dir)
        assert result["old_wiki_dirs"] == ["wiki1", "wiki2"]

    def test_files_under_config_are_ignored(self, probe, instance_dir):
        (instance_dir / "config" / "wikis.yaml").write_text("wikis: []\n")
        result = _run(probe, instance_dir)
        assert result["old_wiki_dirs"] == []


class TestStrayPhp:
    def test_no_php_files(self, probe, instance_dir):
        result = _run(probe, instance_dir)
        assert result["stray_php"] == []

    def test_php_files_listed(self, probe, instance_dir):
        (instance_dir / "config" / "settings" / "Foo.php").write_text("<?php\n")
        (instance_dir / "config" / "settings" / "Bar.php").write_text("<?php\n")
        result = _run(probe, instance_dir)
        names = sorted(os.path.basename(p) for p in result["stray_php"])
        assert names == ["Bar.php", "Foo.php"]

    def test_php_in_subdirs_not_listed(self, probe, instance_dir):
        """global/ and wikis/<id>/ subdirs already have the right
        layout; only loose .php under config/settings/ counts as stray."""
        (instance_dir / "config" / "settings" / "global").mkdir()
        (instance_dir / "config" / "settings" / "global" / "Foo.php").write_text("<?php\n")
        result = _run(probe, instance_dir)
        assert result["stray_php"] == []


class TestHostDirs:
    def test_no_hosts(self, probe, instance_dir):
        result = _run(probe, instance_dir)
        assert result["host_dirs"] == []

    def test_lists_per_host_dirs_excluding_shared(self, probe, instance_dir):
        (instance_dir / "hosts").mkdir()
        for d in ("host1", "host2", "_shared"):
            (instance_dir / "hosts" / d).mkdir()
        result = _run(probe, instance_dir)
        assert result["host_dirs"] == ["host1", "host2"]
