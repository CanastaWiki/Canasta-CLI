"""Tests for the canasta_registry Ansible module."""

import json
import os

import canasta_registry
from mock_ansible import run_module_with_params


class TestGetConfigDir:
    def test_override(self):
        assert canasta_registry.get_config_dir("/custom/path") == "/custom/path"

    def test_env_variable(self, monkeypatch, tmp_dir):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", tmp_dir)
        assert canasta_registry.get_config_dir() == tmp_dir

    def test_default_non_root_linux(self, monkeypatch, tmp_dir):
        monkeypatch.delenv("CANASTA_CONFIG_DIR", raising=False)
        monkeypatch.setattr(canasta_registry, "is_root", lambda: False)
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", tmp_dir)
        assert canasta_registry.get_config_dir() == os.path.join(tmp_dir, "canasta")

    def test_default_non_root_macos(self, monkeypatch):
        monkeypatch.delenv("CANASTA_CONFIG_DIR", raising=False)
        monkeypatch.setattr(canasta_registry, "is_root", lambda: False)
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        expected = os.path.join(
            os.path.expanduser("~"),
            "Library", "Application Support", "canasta",
        )
        assert canasta_registry.get_config_dir() == expected

    def test_default_root(self, monkeypatch):
        monkeypatch.delenv("CANASTA_CONFIG_DIR", raising=False)
        monkeypatch.setattr(canasta_registry, "is_root", lambda: True)
        assert canasta_registry.get_config_dir() == "/etc/canasta"


class TestReadConfig:
    def test_missing_file_returns_empty(self, tmp_dir):
        result = canasta_registry.read_config(tmp_dir)
        assert result == {"Instances": {}}

    def test_reads_existing_config(self, sample_config):
        config_dir, expected = sample_config
        result = canasta_registry.read_config(config_dir)
        assert "mysite" in result["Instances"]
        assert "devsite" in result["Instances"]

    def test_migrates_legacy_installations_key(self, tmp_dir):
        legacy = {"Installations": {"old": {"id": "old", "path": "/old", "orchestrator": "compose"}}}
        with open(os.path.join(tmp_dir, "conf.json"), "w") as f:
            json.dump(legacy, f)
        result = canasta_registry.read_config(tmp_dir)
        assert "old" in result["Instances"]
        assert "Installations" not in result


class TestWriteConfig:
    def test_creates_directory_and_file(self, tmp_dir):
        config_dir = os.path.join(tmp_dir, "new_dir")
        canasta_registry.write_config(config_dir, {"Instances": {}})
        assert os.path.exists(os.path.join(config_dir, "conf.json"))

    def test_writes_valid_json(self, tmp_dir):
        data = {"Instances": {"test": {"id": "test", "path": "/test", "orchestrator": "compose"}}}
        canasta_registry.write_config(tmp_dir, data)
        with open(os.path.join(tmp_dir, "conf.json")) as f:
            loaded = json.load(f)
        assert loaded["Instances"]["test"]["id"] == "test"


class TestInstanceToDict:
    def test_minimal_instance(self):
        result = canasta_registry.instance_to_dict({"id": "test", "path": "/test"})
        assert result == {"id": "test", "path": "/test", "orchestrator": "compose"}

    def test_omits_false_booleans(self):
        result = canasta_registry.instance_to_dict({
            "id": "test", "path": "/test", "devMode": False, "managedCluster": False
        })
        assert "devMode" not in result
        assert "managedCluster" not in result

    def test_includes_true_booleans(self):
        result = canasta_registry.instance_to_dict({
            "id": "test", "path": "/test", "devMode": True
        })
        assert result["devMode"] is True

    def test_includes_optional_strings(self):
        result = canasta_registry.instance_to_dict({
            "id": "test", "path": "/test", "registry": "localhost:5000", "buildFrom": "/src"
        })
        assert result["registry"] == "localhost:5000"
        assert result["buildFrom"] == "/src"

    def test_includes_host(self):
        result = canasta_registry.instance_to_dict({
            "id": "test", "path": "/test", "host": "prod1.example.com"
        })
        assert result["host"] == "prod1.example.com"

    def test_omits_empty_host(self):
        result = canasta_registry.instance_to_dict({
            "id": "test", "path": "/test", "host": ""
        })
        assert "host" not in result

    def test_omits_none_host(self):
        result = canasta_registry.instance_to_dict({
            "id": "test", "path": "/test"
        })
        assert "host" not in result


class TestFindByPath:
    def test_exact_match(self, sample_config):
        config_dir, data = sample_config
        instances = data["Instances"]
        mysite_path = instances["mysite"]["path"]
        found_id, found_inst = canasta_registry.find_by_path(instances, mysite_path)
        assert found_id == "mysite"

    def test_subdirectory_match(self, sample_config):
        config_dir, data = sample_config
        instances = data["Instances"]
        sub_path = os.path.join(instances["mysite"]["path"], "config", "settings")
        os.makedirs(sub_path, exist_ok=True)
        found_id, _ = canasta_registry.find_by_path(instances, sub_path)
        assert found_id == "mysite"

    def test_no_match(self, sample_config):
        _, data = sample_config
        found_id, found_inst = canasta_registry.find_by_path(data["Instances"], "/nonexistent")
        assert found_id is None
        assert found_inst is None


class TestRunModuleQuery:
    def test_query_existing(self, sample_config):
        config_dir, _ = sample_config
        result, failed, _ = run_module_with_params(canasta_registry, {
            "state": "query", "id": "mysite", "path": None,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": config_dir,
        })
        assert not failed
        assert result["instance"]["id"] == "mysite"

    def test_query_missing(self, sample_config):
        config_dir, _ = sample_config
        result, failed, msg = run_module_with_params(canasta_registry, {
            "state": "query", "id": "nonexistent", "path": None,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": config_dir,
        })
        assert failed
        assert "not found" in msg


class TestRunModuleQueryAll:
    def test_query_all(self, sample_config):
        config_dir, _ = sample_config
        result, failed, _ = run_module_with_params(canasta_registry, {
            "state": "query_all", "id": None, "path": None,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": config_dir,
        })
        assert not failed
        assert "mysite" in result["instances"]
        assert "devsite" in result["instances"]

    def test_query_all_filter_host(self, tmp_dir):
        data = {"Instances": {
            "a": {"id": "a", "path": "/a", "orchestrator": "compose", "host": "host1"},
            "b": {"id": "b", "path": "/b", "orchestrator": "compose", "host": "host2"},
        }}
        with open(os.path.join(tmp_dir, "conf.json"), "w") as f:
            json.dump(data, f)
        result, failed, _ = run_module_with_params(canasta_registry, {
            "state": "query_all", "id": None, "path": None,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": "host1",
            "config_dir": tmp_dir,
        })
        assert not failed
        assert "a" in result["instances"]
        assert "b" not in result["instances"]


class TestRunModulePresent:
    def test_add_new(self, tmp_dir):
        inst_path = os.path.join(tmp_dir, "new")
        os.makedirs(inst_path)
        result, failed, _ = run_module_with_params(canasta_registry, {
            "state": "present", "id": "new", "path": inst_path,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": "localhost", "filter_host": None,
            "config_dir": tmp_dir,
        })
        assert not failed
        assert result["changed"]
        assert result["instance"]["host"] == "localhost"

    def test_add_idempotent(self, tmp_dir):
        inst_path = os.path.join(tmp_dir, "idem")
        os.makedirs(inst_path)
        params = {
            "state": "present", "id": "idem", "path": inst_path,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": tmp_dir,
        }
        run_module_with_params(canasta_registry, params.copy())
        result, failed, _ = run_module_with_params(canasta_registry, params.copy())
        assert not failed
        assert not result["changed"]


class TestRunModuleAbsent:
    def test_delete_existing(self, sample_config):
        config_dir, _ = sample_config
        result, failed, _ = run_module_with_params(canasta_registry, {
            "state": "absent", "id": "mysite", "path": None,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": config_dir,
        })
        assert not failed
        assert result["changed"]

    def test_delete_nonexistent(self, sample_config):
        config_dir, _ = sample_config
        result, failed, _ = run_module_with_params(canasta_registry, {
            "state": "absent", "id": "nonexistent", "path": None,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": config_dir,
        })
        assert not failed
        assert not result["changed"]


class TestRunModuleCleanup:
    def test_cleanup_removes_stale(self, tmp_dir):
        data = {"Instances": {
            "good": {"id": "good", "path": os.path.join(tmp_dir, "good"), "orchestrator": "compose"},
            "stale": {"id": "stale", "path": "/nonexistent/path", "orchestrator": "compose"},
        }}
        os.makedirs(os.path.join(tmp_dir, "good"))
        with open(os.path.join(tmp_dir, "conf.json"), "w") as f:
            json.dump(data, f)
        result, failed, _ = run_module_with_params(canasta_registry, {
            "state": "cleanup", "id": None, "path": None,
            "orchestrator": "compose", "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": tmp_dir,
        })
        assert not failed
        assert result["changed"]
        assert "stale" in result["removed"]


class TestRunModuleQueryByPath:
    def test_query_by_path(self, sample_config):
        config_dir, data = sample_config
        mysite_path = data["Instances"]["mysite"]["path"]
        result, failed, _ = run_module_with_params(canasta_registry, {
            "state": "query_by_path", "id": None, "path": mysite_path,
            "orchestrator": None, "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": config_dir,
        })
        assert not failed
        assert result["instance"]["id"] == "mysite"

    def test_query_by_path_not_found(self, sample_config):
        config_dir, _ = sample_config
        result, failed, msg = run_module_with_params(canasta_registry, {
            "state": "query_by_path", "id": None, "path": "/nonexistent",
            "orchestrator": None, "dev_mode": False,
            "managed_cluster": False, "registry": None,
            "kind_cluster": None, "build_from": None,
            "host": None, "filter_host": None,
            "config_dir": config_dir,
        })
        assert failed
        assert "No instance found" in msg
