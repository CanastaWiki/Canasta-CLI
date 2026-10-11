"""Error branches of run_module in canasta_wikis_yaml and canasta_registry."""

import json
import os

import pytest
import yaml

import canasta_registry
import canasta_wikis_yaml
from mock_ansible import run_module_with_params


def _write_wikis(instance_path, content):
    os.makedirs(os.path.join(instance_path, "config"), exist_ok=True)
    with open(os.path.join(instance_path, "config", "wikis.yaml"), "w") as f:
        f.write(content)


TWO_WIKIS = (
    "wikis:\n"
    "- id: main\n  url: example.com\n  name: main\n"
    "- id: docs\n  url: example.com/docs\n  name: docs\n"
)
ONE_WIKI = "wikis:\n- id: main\n  url: example.com\n  name: main\n"
BAD_EXTRA = (
    "wikis:\n- id: main\n  url: example.com\n  extra_databases: cargo\n"
)


class TestWikisYamlRunModuleErrors:
    @pytest.mark.parametrize("params,content,msg", [
        ({"state": "query"}, TWO_WIKIS, "wiki_id is required for query"),
        ({"state": "generate", "domain": "example.com"}, None,
         "wiki_id is required for generate"),
        ({"state": "generate", "wiki_id": "main"}, None,
         "domain is required for generate"),
        ({"state": "generate", "wiki_id": "bad-id", "domain": "example.com"},
         None, "wiki ID 'bad-id' cannot contain hyphens"),
        ({"state": "add", "domain": "example.com"}, TWO_WIKIS,
         "wiki_id is required for add"),
        ({"state": "add", "wiki_id": "new"}, TWO_WIKIS,
         "domain is required for add"),
        ({"state": "add", "wiki_id": "no way", "domain": "example.com"},
         TWO_WIKIS, "wiki ID 'no way' is invalid"),
        ({"state": "add", "wiki_id": "new", "domain": "example.com",
          "wiki_path": "docs"}, TWO_WIKIS,
         "Wiki URL 'example.com/docs' already exists"),
        ({"state": "remove"}, TWO_WIKIS, "wiki_id is required for remove"),
        ({"state": "remove", "wiki_id": "gone"}, TWO_WIKIS,
         "Wiki ID 'gone' not found"),
        ({"state": "remove", "wiki_id": "main"}, ONE_WIKI,
         "Cannot remove the last wiki"),
        ({"state": "add_database", "database": "cargo"}, TWO_WIKIS,
         "wiki_id is required for add_database"),
        ({"state": "remove_database", "wiki_id": "main"}, TWO_WIKIS,
         "database is required for remove_database"),
        ({"state": "add_database", "wiki_id": "main", "database": "a b"},
         TWO_WIKIS, "extra database name 'a b' is invalid"),
        ({"state": "add_database", "wiki_id": "gone", "database": "cargo"},
         TWO_WIKIS, "Wiki ID 'gone' not found (present: main, docs)"),
        ({"state": "add_database", "wiki_id": "main", "database": "main"},
         TWO_WIKIS, "'main' is the wiki's own database"),
        ({"state": "add_database", "wiki_id": "main", "database": "cargo"},
         BAD_EXTRA, "extra_databases for wiki 'main' must be a list"),
        ({"state": "update_port"}, TWO_WIKIS,
         "port is required for update_port"),
        ({"state": "update_domain", "old_domain": "example.com"}, TWO_WIKIS,
         "domain is required for update_domain"),
        ({"state": "update_domain", "domain": "new.example.com"}, TWO_WIKIS,
         "old_domain is required for update_domain"),
        ({"state": "read"}, BAD_EXTRA,
         "extra_databases for wiki 'main' must be a list"),
        ({"state": "query", "wiki_id": "main"}, BAD_EXTRA,
         "extra_databases for wiki 'main' must be a list"),
    ])
    def test_fails_with_message(self, tmp_dir, params, content, msg):
        if content is not None:
            _write_wikis(tmp_dir, content)
        before = (open(os.path.join(tmp_dir, "config", "wikis.yaml")).read()
                  if content is not None else None)
        _, failed, fail_msg = run_module_with_params(
            canasta_wikis_yaml, dict(params, instance_path=tmp_dir))
        assert failed
        assert msg in fail_msg
        after_path = os.path.join(tmp_dir, "config", "wikis.yaml")
        if before is None:
            assert not os.path.exists(after_path)
        else:
            assert open(after_path).read() == before

    @pytest.mark.xfail(
        reason="an unparseable wikis.yaml raises yaml.YAMLError out of "
               "run_module (a module traceback) instead of fail_json",
        raises=yaml.YAMLError, strict=True,
    )
    def test_unparseable_wikis_yaml_fails_cleanly(self, tmp_dir):
        _write_wikis(tmp_dir, "wikis: [\n")
        _, failed, fail_msg = run_module_with_params(
            canasta_wikis_yaml, {"state": "read", "instance_path": tmp_dir})
        assert failed
        assert "wikis.yaml" in fail_msg


class TestRegistryRunModuleErrors:
    def _config(self, tmp_dir, instances=None, raw=None):
        with open(os.path.join(tmp_dir, "conf.json"), "w") as f:
            f.write(raw if raw is not None
                    else json.dumps({"Instances": instances or {}}))

    @pytest.mark.parametrize("params,msg", [
        ({"state": "query"},
         "Instance ID is required for query (or provide path to auto-detect)"),
        ({"state": "query", "id": "gone"},
         "Instance 'gone' not found in registry"),
        ({"state": "query", "path": "/nowhere"},
         "Instance ID is required for query"),
        ({"state": "query_by_path"}, "path is required for query_by_path"),
        ({"state": "query_by_path", "path": "/nowhere"},
         "No instance found for path '/nowhere'"),
        ({"state": "present", "path": "/srv/x"},
         "id is required when state=present"),
        ({"state": "present", "id": "x"},
         "path is required when state=present"),
        ({"state": "update"}, "id is required when state=update"),
        ({"state": "update", "id": "gone", "host": "h"},
         "Instance 'gone' not found in registry"),
        ({"state": "absent"}, "id is required when state=absent"),
        ({"state": "get_setting"}, "setting_key is required for get_setting"),
        ({"state": "set_setting", "setting_value": "v"},
         "setting_key is required for set_setting"),
    ])
    def test_fails_with_message(self, tmp_dir, params, msg):
        self._config(tmp_dir, {"mysite": {"path": "/srv/mysite"}})
        before = open(os.path.join(tmp_dir, "conf.json")).read()
        _, failed, fail_msg = run_module_with_params(
            canasta_registry,
            dict({"orchestrator": None, "dev_mode": None,
                  "managed_cluster": None},
                 **params, config_dir=tmp_dir))
        assert failed
        assert msg in fail_msg
        assert open(os.path.join(tmp_dir, "conf.json")).read() == before

    def test_malformed_conf_json(self, tmp_dir):
        self._config(tmp_dir, raw="{not json")
        _, failed, fail_msg = run_module_with_params(
            canasta_registry, {"state": "query_all", "config_dir": tmp_dir})
        assert failed
        assert fail_msg.startswith("Failed to read config: ")

    @pytest.mark.xfail(
        reason="a conf.json whose top level is not an object raises "
               "TypeError out of run_module instead of fail_json",
        raises=TypeError, strict=True,
    )
    def test_non_object_conf_json(self, tmp_dir):
        self._config(tmp_dir, raw="[]")
        _, failed, fail_msg = run_module_with_params(
            canasta_registry, {"state": "query_all", "config_dir": tmp_dir})
        assert failed
