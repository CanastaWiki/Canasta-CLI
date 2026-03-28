"""Tests for the canasta_wikis_yaml Ansible module."""

import os

import canasta_wikis_yaml


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
