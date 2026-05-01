"""Tests for the canasta_wikis_yaml Ansible module."""

import os

import canasta_wikis_yaml
from mock_ansible import run_module_with_params


class TestValidateWikiId:
    def test_valid_id(self):
        assert canasta_wikis_yaml.validate_wiki_id("main") is None

    def test_valid_id_with_underscore(self):
        assert canasta_wikis_yaml.validate_wiki_id("my_wiki") is None

    def test_empty_id(self):
        err = canasta_wikis_yaml.validate_wiki_id("")
        assert "empty" in err

    def test_hyphen_rejected(self):
        err = canasta_wikis_yaml.validate_wiki_id("my-wiki")
        assert "hyphen" in err

    def test_reserved_settings(self):
        err = canasta_wikis_yaml.validate_wiki_id("settings")
        assert "reserved" in err

    def test_reserved_images(self):
        err = canasta_wikis_yaml.validate_wiki_id("images")
        assert "reserved" in err

    def test_reserved_w(self):
        assert canasta_wikis_yaml.validate_wiki_id("w") is not None

    def test_reserved_wiki(self):
        assert canasta_wikis_yaml.validate_wiki_id("wiki") is not None

    def test_reserved_wikis(self):
        assert canasta_wikis_yaml.validate_wiki_id("wikis") is not None


class TestBuildUrl:
    def test_domain_only(self):
        assert canasta_wikis_yaml.build_url("example.com") == "example.com"

    def test_domain_with_path(self):
        assert canasta_wikis_yaml.build_url("example.com", "docs") == "example.com/docs"

    def test_strips_trailing_slash(self):
        assert canasta_wikis_yaml.build_url("example.com/", "docs") == "example.com/docs"

    def test_strips_leading_slash_on_path(self):
        assert canasta_wikis_yaml.build_url("example.com", "/docs") == "example.com/docs"


class TestParseUrl:
    def test_domain_only(self):
        server, path = canasta_wikis_yaml.parse_url("example.com")
        assert server == "example.com"
        assert path == ""

    def test_domain_with_path(self):
        server, path = canasta_wikis_yaml.parse_url("example.com/docs")
        assert server == "example.com"
        assert path == "/docs"

    def test_deep_path(self):
        server, path = canasta_wikis_yaml.parse_url("example.com/path/to/wiki")
        assert server == "example.com"
        assert path == "/path/to/wiki"


class TestReadWriteWikis:
    def test_read_nonexistent_returns_empty(self, tmp_dir):
        wikis = canasta_wikis_yaml.read_wikis(tmp_dir)
        assert wikis == []

    def test_read_existing(self, sample_wikis_yaml):
        wikis = canasta_wikis_yaml.read_wikis(sample_wikis_yaml)
        assert len(wikis) == 2
        assert wikis[0]["id"] == "main"
        assert wikis[1]["id"] == "docs"

    def test_write_and_read_roundtrip(self, tmp_dir):
        wikis = [
            {"id": "test", "url": "example.com", "name": "Test Wiki"},
        ]
        canasta_wikis_yaml.write_wikis(tmp_dir, wikis)
        result = canasta_wikis_yaml.read_wikis(tmp_dir)
        assert len(result) == 1
        assert result[0]["id"] == "test"

    def test_write_creates_directory(self, tmp_dir):
        dest = os.path.join(tmp_dir, "newinstance")
        wikis = [{"id": "x", "url": "a.com", "name": "X"}]
        canasta_wikis_yaml.write_wikis(dest, wikis)
        assert os.path.exists(os.path.join(dest, "config", "wikis.yaml"))


class TestGetWikiIds:
    def test_returns_ids(self, sample_wikis_yaml):
        wikis = canasta_wikis_yaml.read_wikis(sample_wikis_yaml)
        ids = canasta_wikis_yaml.get_wiki_ids(wikis)
        assert ids == ["main", "docs"]


class TestWikiIdExists:
    def test_exists(self, sample_wikis_yaml):
        wikis = canasta_wikis_yaml.read_wikis(sample_wikis_yaml)
        assert canasta_wikis_yaml.wiki_id_exists(wikis, "main") is True

    def test_not_exists(self, sample_wikis_yaml):
        wikis = canasta_wikis_yaml.read_wikis(sample_wikis_yaml)
        assert canasta_wikis_yaml.wiki_id_exists(wikis, "nope") is False


class TestWikiUrlExists:
    def test_exists(self, sample_wikis_yaml):
        wikis = canasta_wikis_yaml.read_wikis(sample_wikis_yaml)
        assert canasta_wikis_yaml.wiki_url_exists(wikis, "example.com") is True

    def test_exists_with_path(self, sample_wikis_yaml):
        wikis = canasta_wikis_yaml.read_wikis(sample_wikis_yaml)
        assert canasta_wikis_yaml.wiki_url_exists(wikis, "example.com", "docs") is True

    def test_not_exists(self, sample_wikis_yaml):
        wikis = canasta_wikis_yaml.read_wikis(sample_wikis_yaml)
        assert canasta_wikis_yaml.wiki_url_exists(wikis, "other.com") is False


class TestUpdateUrlPort:
    def test_standard_port_stripped(self):
        assert canasta_wikis_yaml.update_url_port("example.com:8443", "443") == "example.com"

    def test_nonstandard_port_appended(self):
        assert canasta_wikis_yaml.update_url_port("example.com", "8443") == "example.com:8443"

    def test_port_replaced(self):
        assert canasta_wikis_yaml.update_url_port("example.com:8443", "9443") == "example.com:9443"

    def test_preserves_path(self):
        assert canasta_wikis_yaml.update_url_port("example.com:8443/docs", "9443") == "example.com:9443/docs"

    def test_preserves_path_standard_port(self):
        assert canasta_wikis_yaml.update_url_port("example.com:8443/docs", "443") == "example.com/docs"

    def test_no_existing_port_with_path(self):
        assert canasta_wikis_yaml.update_url_port("example.com/docs", "8443") == "example.com:8443/docs"

    def test_no_change_needed(self):
        assert canasta_wikis_yaml.update_url_port("example.com", "443") == "example.com"

    # default_port: HTTP scheme uses 80 as the implicit default
    def test_http_standard_port_stripped(self):
        assert canasta_wikis_yaml.update_url_port(
            "example.com:9080", "80", default_port="80"
        ) == "example.com"

    def test_http_nonstandard_port_appended(self):
        assert canasta_wikis_yaml.update_url_port(
            "example.com", "8080", default_port="80"
        ) == "example.com:8080"

    def test_http_port_replaced(self):
        assert canasta_wikis_yaml.update_url_port(
            "example.com:8080", "9080", default_port="80"
        ) == "example.com:9080"

    def test_http_preserves_path_standard_port(self):
        assert canasta_wikis_yaml.update_url_port(
            "example.com:8080/docs", "80", default_port="80"
        ) == "example.com/docs"

    def test_http_443_kept_when_default_is_80(self):
        # 443 is non-standard for HTTP scheme, so it should be appended
        assert canasta_wikis_yaml.update_url_port(
            "example.com", "443", default_port="80"
        ) == "example.com:443"


class TestUpdateUrlDomain:
    def test_simple_domain_change(self):
        assert canasta_wikis_yaml.update_url_domain("old.com", "new.com") == "new.com"

    def test_preserves_path(self):
        assert canasta_wikis_yaml.update_url_domain("old.com/docs", "new.com") == "new.com/docs"

    def test_preserves_existing_port(self):
        assert canasta_wikis_yaml.update_url_domain("old.com:8443", "new.com") == "new.com:8443"

    def test_preserves_port_and_path(self):
        assert canasta_wikis_yaml.update_url_domain("old.com:8443/docs", "new.com") == "new.com:8443/docs"

    def test_new_domain_with_port(self):
        assert canasta_wikis_yaml.update_url_domain("old.com", "new.com:9443") == "new.com:9443"

    def test_new_domain_with_port_overrides_old_port(self):
        assert canasta_wikis_yaml.update_url_domain("old.com:8443", "new.com:9443") == "new.com:9443"

    def test_new_domain_with_port_preserves_path(self):
        assert canasta_wikis_yaml.update_url_domain("old.com:8443/docs", "new.com:9443") == "new.com:9443/docs"

    def test_deep_path(self):
        assert canasta_wikis_yaml.update_url_domain("old.com/path/to/wiki", "new.com") == "new.com/path/to/wiki"

    def test_same_domain_no_change(self):
        assert canasta_wikis_yaml.update_url_domain("example.com", "example.com") == "example.com"

    def test_same_domain_preserves_path(self):
        assert canasta_wikis_yaml.update_url_domain("example.com/docs", "example.com") == "example.com/docs"


class TestRunModuleRead:
    def test_read(self, sample_wikis_yaml):
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "read",
            "wiki_id": None, "domain": None, "wiki_path": None, "site_name": None,
        })
        assert not failed
        assert len(result["wikis"]) == 2
        assert result["wiki_ids"] == ["main", "docs"]


class TestRunModuleGenerate:
    def test_generate(self, tmp_dir):
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "generate",
            "wiki_id": "test", "domain": "example.com",
            "wiki_path": None, "site_name": "Test Wiki",
        })
        assert not failed
        assert result["changed"]

    def test_generate_rejects_hyphen(self, tmp_dir):
        result, failed, msg = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "generate",
            "wiki_id": "my-wiki", "domain": "example.com",
            "wiki_path": None, "site_name": None,
        })
        assert failed
        assert "hyphen" in msg


class TestRunModuleAdd:
    def test_add_wiki(self, sample_wikis_yaml):
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "add",
            "wiki_id": "blog", "domain": "example.com",
            "wiki_path": "blog", "site_name": "Blog",
        })
        assert not failed
        assert result["changed"]
        assert len(result["wikis"]) == 3

    def test_add_duplicate_fails(self, sample_wikis_yaml):
        result, failed, msg = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "add",
            "wiki_id": "main", "domain": "example.com",
            "wiki_path": None, "site_name": None,
        })
        assert failed
        assert "already exists" in msg


class TestRunModuleRemove:
    def test_remove_wiki(self, sample_wikis_yaml):
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "remove",
            "wiki_id": "docs", "domain": None,
            "wiki_path": None, "site_name": None,
        })
        assert not failed
        assert result["changed"]
        assert len(result["wikis"]) == 1

    def test_remove_last_fails(self, tmp_dir):
        # Create single-wiki yaml
        os.makedirs(os.path.join(tmp_dir, "config"), exist_ok=True)
        canasta_wikis_yaml.write_wikis(tmp_dir, [{"id": "only", "url": "a.com", "name": "Only"}])
        result, failed, msg = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "remove",
            "wiki_id": "only", "domain": None,
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert failed
        assert "last wiki" in msg


class TestRunModuleUpdatePort:
    def test_update_port_single_wiki(self, tmp_dir):
        os.makedirs(os.path.join(tmp_dir, "config"), exist_ok=True)
        canasta_wikis_yaml.write_wikis(tmp_dir, [
            {"id": "main", "url": "localhost", "name": "Main"},
        ])
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "update_port",
            "wiki_id": None, "domain": None,
            "wiki_path": None, "site_name": None,
            "port": "8443",
        })
        assert not failed
        assert result["changed"]
        assert result["wikis"][0]["url"] == "localhost:8443"

    def test_update_port_multiple_wikis(self, sample_wikis_yaml):
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "update_port",
            "wiki_id": None, "domain": None,
            "wiki_path": None, "site_name": None,
            "port": "9443",
        })
        assert not failed
        assert result["wikis"][0]["url"] == "example.com:9443"
        assert result["wikis"][1]["url"] == "example.com:9443/docs"

    def test_update_port_to_standard(self, tmp_dir):
        os.makedirs(os.path.join(tmp_dir, "config"), exist_ok=True)
        canasta_wikis_yaml.write_wikis(tmp_dir, [
            {"id": "main", "url": "localhost:8443", "name": "Main"},
        ])
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "update_port",
            "wiki_id": None, "domain": None,
            "wiki_path": None, "site_name": None,
            "port": "443",
        })
        assert not failed
        assert result["wikis"][0]["url"] == "localhost"

    def test_update_port_requires_port(self, sample_wikis_yaml):
        result, failed, msg = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "update_port",
            "wiki_id": None, "domain": None,
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert failed
        assert "port is required" in msg


class TestRunModuleUpdateDomain:
    def test_update_domain_single_wiki(self, tmp_dir):
        os.makedirs(os.path.join(tmp_dir, "config"), exist_ok=True)
        canasta_wikis_yaml.write_wikis(tmp_dir, [
            {"id": "main", "url": "old.example.com", "name": "Main"},
        ])
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "update_domain",
            "wiki_id": None, "domain": "new.example.com",
            "old_domain": "old.example.com",
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert not failed
        assert result["changed"]
        assert result["wikis"][0]["url"] == "new.example.com"

    def test_update_domain_multiple_wikis(self, sample_wikis_yaml):
        # sample_wikis_yaml fixture: both wikis on example.com.
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "update_domain",
            "wiki_id": None, "domain": "newsite.com",
            "old_domain": "example.com",
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert not failed
        assert result["changed"]
        assert result["wikis"][0]["url"] == "newsite.com"
        assert result["wikis"][1]["url"] == "newsite.com/docs"

    def test_update_domain_preserves_port(self, tmp_dir):
        os.makedirs(os.path.join(tmp_dir, "config"), exist_ok=True)
        canasta_wikis_yaml.write_wikis(tmp_dir, [
            {"id": "main", "url": "old.com:8443", "name": "Main"},
            {"id": "docs", "url": "old.com:8443/docs", "name": "Docs"},
        ])
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "update_domain",
            "wiki_id": None, "domain": "new.com",
            "old_domain": "old.com",
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert not failed
        assert result["wikis"][0]["url"] == "new.com:8443"
        assert result["wikis"][1]["url"] == "new.com:8443/docs"

    def test_update_domain_requires_domain(self, sample_wikis_yaml):
        result, failed, msg = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "update_domain",
            "wiki_id": None, "domain": None,
            "old_domain": "example.com",
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert failed
        assert "domain is required" in msg

    def test_update_domain_requires_old_domain(self, sample_wikis_yaml):
        # Without old_domain, the module must refuse — preventing the
        # multi-domain clobber bug from #445.
        result, failed, msg = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": sample_wikis_yaml, "state": "update_domain",
            "wiki_id": None, "domain": "newsite.com",
            "old_domain": None,
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert failed
        assert "old_domain is required" in msg

    def test_update_domain_filters_other_domains(self, tmp_dir):
        # Multi-domain instance: changing the FQDN of one wiki must not
        # touch wikis on unrelated domains. Regression test for #445.
        os.makedirs(os.path.join(tmp_dir, "config"), exist_ok=True)
        canasta_wikis_yaml.write_wikis(tmp_dir, [
            {"id": "main", "url": "a.example.com", "name": "Main"},
            {"id": "docs", "url": "a.example.com/docs", "name": "Docs"},
            {"id": "side", "url": "unrelated.org", "name": "Side"},
            {"id": "other", "url": "b.example.com", "name": "Other"},
        ])
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "update_domain",
            "wiki_id": None, "domain": "newhost.example.com",
            "old_domain": "a.example.com",
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert not failed
        assert result["changed"]
        urls = {w["id"]: w["url"] for w in result["wikis"]}
        assert urls["main"] == "newhost.example.com"
        assert urls["docs"] == "newhost.example.com/docs"
        # Wikis on other domains untouched:
        assert urls["side"] == "unrelated.org"
        assert urls["other"] == "b.example.com"

    def test_update_domain_no_match_is_noop(self, tmp_dir):
        # If old_domain doesn't match any wiki, the module reports
        # changed=False and the file is unchanged.
        os.makedirs(os.path.join(tmp_dir, "config"), exist_ok=True)
        canasta_wikis_yaml.write_wikis(tmp_dir, [
            {"id": "main", "url": "a.example.com", "name": "Main"},
        ])
        result, failed, _ = run_module_with_params(canasta_wikis_yaml, {
            "instance_path": tmp_dir, "state": "update_domain",
            "wiki_id": None, "domain": "newhost.example.com",
            "old_domain": "completely-different.com",
            "wiki_path": None, "site_name": None,
            "port": None,
        })
        assert not failed
        assert not result["changed"]
        assert result["wikis"][0]["url"] == "a.example.com"
