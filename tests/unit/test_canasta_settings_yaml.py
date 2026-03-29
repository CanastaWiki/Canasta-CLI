"""Tests for the canasta_settings_yaml Ansible module."""

import os

import canasta_settings_yaml
from mock_ansible import run_module_with_params


class TestConfigPath:
    def test_global(self):
        path = canasta_settings_yaml.config_path("/inst")
        assert path == "/inst/config/settings/global/settings.yaml"

    def test_per_wiki(self):
        path = canasta_settings_yaml.config_path("/inst", "mywiki")
        assert path == "/inst/config/settings/wikis/mywiki/settings.yaml"


class TestValidateName:
    def test_valid(self):
        assert canasta_settings_yaml.validate_name("VisualEditor") is None

    def test_empty(self):
        assert canasta_settings_yaml.validate_name("") is not None

    def test_invalid_start(self):
        assert canasta_settings_yaml.validate_name(".bad") is not None


class TestReadWriteConfig:
    def test_read_nonexistent(self, tmp_dir):
        path = os.path.join(tmp_dir, "settings.yaml")
        config = canasta_settings_yaml.read_config(path)
        assert config == {"extensions": [], "skins": []}

    def test_read_existing(self, sample_settings_yaml):
        path = canasta_settings_yaml.config_path(sample_settings_yaml)
        config = canasta_settings_yaml.read_config(path)
        assert "Cite" in config["extensions"]
        assert "VisualEditor" in config["extensions"]
        assert "Timeless" in config["skins"]
        assert "Vector" in config["skins"]

    def test_write_and_read(self, tmp_dir):
        path = os.path.join(tmp_dir, "config", "settings", "global", "settings.yaml")
        config = {"extensions": ["Cite", "VisualEditor"], "skins": ["Vector"]}
        canasta_settings_yaml.write_config(path, config)
        result = canasta_settings_yaml.read_config(path)
        assert result["extensions"] == ["Cite", "VisualEditor"]
        assert result["skins"] == ["Vector"]

    def test_write_includes_header(self, tmp_dir):
        path = os.path.join(tmp_dir, "settings.yaml")
        config = {"extensions": ["Cite"], "skins": []}
        canasta_settings_yaml.write_config(path, config)
        with open(path) as f:
            content = f.read()
        assert content.startswith("# Canasta will add and remove")

    def test_write_empty_deletes_file(self, tmp_dir):
        path = os.path.join(tmp_dir, "settings.yaml")
        # Write something first
        config = {"extensions": ["Cite"], "skins": []}
        canasta_settings_yaml.write_config(path, config)
        assert os.path.exists(path)
        # Write empty
        canasta_settings_yaml.write_config(path, {"extensions": [], "skins": []})
        assert not os.path.exists(path)


class TestEnable:
    def test_enable_new_extension(self, sample_settings_yaml):
        path = canasta_settings_yaml.config_path(sample_settings_yaml)
        config = canasta_settings_yaml.read_config(path)
        items = config["extensions"]
        assert "ParserFunctions" not in items
        items.append("ParserFunctions")
        items.sort()
        config["extensions"] = items
        canasta_settings_yaml.write_config(path, config)
        result = canasta_settings_yaml.read_config(path)
        assert "ParserFunctions" in result["extensions"]

    def test_enable_sorts_alphabetically(self, sample_settings_yaml):
        path = canasta_settings_yaml.config_path(sample_settings_yaml)
        config = canasta_settings_yaml.read_config(path)
        config["extensions"].append("AAA")
        config["extensions"].sort()
        assert config["extensions"][0] == "AAA"


class TestDisable:
    def test_disable_extension(self, sample_settings_yaml):
        path = canasta_settings_yaml.config_path(sample_settings_yaml)
        config = canasta_settings_yaml.read_config(path)
        config["extensions"].remove("Cite")
        canasta_settings_yaml.write_config(path, config)
        result = canasta_settings_yaml.read_config(path)
        assert "Cite" not in result["extensions"]
        assert "VisualEditor" in result["extensions"]

    def test_disable_skin(self, sample_settings_yaml):
        path = canasta_settings_yaml.config_path(sample_settings_yaml)
        config = canasta_settings_yaml.read_config(path)
        config["skins"].remove("Timeless")
        canasta_settings_yaml.write_config(path, config)
        result = canasta_settings_yaml.read_config(path)
        assert "Timeless" not in result["skins"]
        assert "Vector" in result["skins"]


class TestRunModuleRead:
    def test_read_extensions(self, sample_settings_yaml):
        result, failed, _ = run_module_with_params(canasta_settings_yaml, {
            "instance_path": sample_settings_yaml, "item_type": "extensions",
            "state": "read", "names": None, "wiki": None,
        })
        assert not failed
        assert "Cite" in result["items"]
        assert "VisualEditor" in result["items"]


class TestRunModuleEnable:
    def test_enable_extension(self, sample_settings_yaml):
        result, failed, _ = run_module_with_params(canasta_settings_yaml, {
            "instance_path": sample_settings_yaml, "item_type": "extensions",
            "state": "enable", "names": ["ParserFunctions"], "wiki": None,
        })
        assert not failed
        assert result["changed"]
        assert "ParserFunctions" in result["items"]

    def test_enable_already_enabled(self, sample_settings_yaml):
        result, failed, _ = run_module_with_params(canasta_settings_yaml, {
            "instance_path": sample_settings_yaml, "item_type": "extensions",
            "state": "enable", "names": ["Cite"], "wiki": None,
        })
        assert not failed
        assert not result["changed"]

    def test_enable_invalid_name(self, sample_settings_yaml):
        result, failed, msg = run_module_with_params(canasta_settings_yaml, {
            "instance_path": sample_settings_yaml, "item_type": "extensions",
            "state": "enable", "names": [".bad"], "wiki": None,
        })
        assert failed
        assert "invalid" in msg


class TestRunModuleDisable:
    def test_disable_extension(self, sample_settings_yaml):
        result, failed, _ = run_module_with_params(canasta_settings_yaml, {
            "instance_path": sample_settings_yaml, "item_type": "extensions",
            "state": "disable", "names": ["Cite"], "wiki": None,
        })
        assert not failed
        assert result["changed"]
        assert "Cite" not in result["items"]

    def test_disable_not_enabled(self, sample_settings_yaml):
        result, failed, msg = run_module_with_params(canasta_settings_yaml, {
            "instance_path": sample_settings_yaml, "item_type": "extensions",
            "state": "disable", "names": ["NotEnabled"], "wiki": None,
        })
        assert failed
        assert "not currently enabled" in msg
