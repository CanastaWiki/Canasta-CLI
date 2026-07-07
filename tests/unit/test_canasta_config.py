"""Tests for the shared canasta_config module_util.

config-dir resolution, conf.json read/write, and path lookup live here
so the canasta_registry Ansible module and the canasta.py CLI wrapper
share one implementation (and can never disagree about where the
registry lives). These were previously part of test_canasta_registry;
they moved with the code.
"""

import json
import os

import canasta_config


class TestGetConfigDir:
    def test_override(self):
        assert canasta_config.get_config_dir("/custom/path") == "/custom/path"

    def test_env_variable(self, monkeypatch, tmp_dir):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", tmp_dir)
        assert canasta_config.get_config_dir() == tmp_dir

    def test_default_non_root_linux(self, monkeypatch, tmp_dir):
        monkeypatch.delenv("CANASTA_CONFIG_DIR", raising=False)
        monkeypatch.setattr(canasta_config, "is_root", lambda: False)
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", tmp_dir)
        assert canasta_config.get_config_dir() == os.path.join(tmp_dir, "canasta")

    def test_default_non_root_macos(self, monkeypatch):
        monkeypatch.delenv("CANASTA_CONFIG_DIR", raising=False)
        monkeypatch.setattr(canasta_config, "is_root", lambda: False)
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        expected = os.path.join(
            os.path.expanduser("~"),
            "Library", "Application Support", "canasta",
        )
        assert canasta_config.get_config_dir() == expected

    def test_default_root(self, monkeypatch):
        monkeypatch.delenv("CANASTA_CONFIG_DIR", raising=False)
        monkeypatch.setattr(canasta_config, "is_root", lambda: True)
        assert canasta_config.get_config_dir() == "/etc/canasta"


class TestConfigPath:
    def test_config_path(self):
        assert canasta_config.config_path("/etc/canasta") == "/etc/canasta/conf.json"


class TestReadConfig:
    def test_missing_file_returns_empty(self, tmp_dir):
        result = canasta_config.read_config(tmp_dir)
        assert result == {"Instances": {}}

    def test_reads_existing_config(self, sample_config):
        config_dir, expected = sample_config
        result = canasta_config.read_config(config_dir)
        assert "mysite" in result["Instances"]
        assert "devsite" in result["Instances"]

    def test_migrates_legacy_installations_key(self, tmp_dir):
        legacy = {"Installations": {"old": {"id": "old", "path": "/old", "orchestrator": "compose"}}}
        with open(os.path.join(tmp_dir, "conf.json"), "w") as f:
            json.dump(legacy, f)
        result = canasta_config.read_config(tmp_dir)
        assert "old" in result["Instances"]
        assert "Installations" not in result


class TestWriteConfig:
    def test_creates_directory_and_file(self, tmp_dir):
        config_dir = os.path.join(tmp_dir, "new_dir")
        canasta_config.write_config(config_dir, {"Instances": {}})
        assert os.path.exists(os.path.join(config_dir, "conf.json"))

    def test_writes_valid_json(self, tmp_dir):
        data = {"Instances": {"test": {"id": "test", "path": "/test", "orchestrator": "compose"}}}
        canasta_config.write_config(tmp_dir, data)
        with open(os.path.join(tmp_dir, "conf.json")) as f:
            loaded = json.load(f)
        assert loaded["Instances"]["test"]["id"] == "test"

    def test_leaves_no_temp_files_behind(self, tmp_dir):
        canasta_config.write_config(tmp_dir, {"Instances": {}})
        assert os.listdir(tmp_dir) == ["conf.json"]

    def test_failed_write_preserves_existing_and_cleans_temp(self, tmp_dir):
        canasta_config.write_config(tmp_dir, {"Instances": {"a": {"id": "a"}}})
        # object() is not JSON-serializable -> json.dump raises mid-write.
        try:
            canasta_config.write_config(tmp_dir, {"Instances": object()})
        except TypeError:
            pass
        else:
            raise AssertionError("expected a serialization error")
        # The good conf.json is intact and no .tmp turd is left.
        with open(os.path.join(tmp_dir, "conf.json")) as f:
            assert json.load(f)["Instances"]["a"]["id"] == "a"
        assert os.listdir(tmp_dir) == ["conf.json"]


class TestFindByPath:
    def test_exact_match(self, sample_config):
        config_dir, data = sample_config
        instances = data["Instances"]
        mysite_path = instances["mysite"]["path"]
        found_id, found_inst = canasta_config.find_by_path(instances, mysite_path)
        assert found_id == "mysite"

    def test_subdirectory_match(self, sample_config):
        config_dir, data = sample_config
        instances = data["Instances"]
        sub_path = os.path.join(instances["mysite"]["path"], "config", "settings")
        os.makedirs(sub_path, exist_ok=True)
        found_id, _ = canasta_config.find_by_path(instances, sub_path)
        assert found_id == "mysite"

    def test_no_match(self, sample_config):
        _, data = sample_config
        found_id, found_inst = canasta_config.find_by_path(data["Instances"], "/nonexistent")
        assert found_id is None
        assert found_inst is None
