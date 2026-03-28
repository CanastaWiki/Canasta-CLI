"""Tests for the canasta_farmsettings Ansible module."""

import canasta_farmsettings


class TestValidateInstanceId:
    def test_valid_simple(self):
        assert canasta_farmsettings.validate_instance_id("mysite") is None

    def test_valid_with_hyphen(self):
        assert canasta_farmsettings.validate_instance_id("my-site") is None

    def test_valid_with_underscore(self):
        assert canasta_farmsettings.validate_instance_id("my_site") is None

    def test_valid_single_char(self):
        assert canasta_farmsettings.validate_instance_id("a") is None

    def test_valid_numeric(self):
        assert canasta_farmsettings.validate_instance_id("123") is None

    def test_empty(self):
        assert canasta_farmsettings.validate_instance_id("") is not None

    def test_starts_with_hyphen(self):
        assert canasta_farmsettings.validate_instance_id("-bad") is not None

    def test_ends_with_hyphen(self):
        assert canasta_farmsettings.validate_instance_id("bad-") is not None

    def test_special_chars(self):
        assert canasta_farmsettings.validate_instance_id("bad!name") is not None

    def test_spaces(self):
        assert canasta_farmsettings.validate_instance_id("bad name") is not None


class TestValidateWikiId:
    def test_valid(self):
        assert canasta_farmsettings.validate_wiki_id("main") is None

    def test_empty(self):
        assert canasta_farmsettings.validate_wiki_id("") is not None

    def test_hyphen(self):
        assert canasta_farmsettings.validate_wiki_id("my-wiki") is not None

    def test_reserved(self):
        for name in ["settings", "images", "w", "wiki", "wikis"]:
            assert canasta_farmsettings.validate_wiki_id(name) is not None


class TestValidateExtensionName:
    def test_valid(self):
        assert canasta_farmsettings.validate_extension_name("VisualEditor") is None

    def test_valid_with_underscore(self):
        assert canasta_farmsettings.validate_extension_name("Semantic_MediaWiki") is None

    def test_valid_with_dot(self):
        assert canasta_farmsettings.validate_extension_name("ext.v2") is None

    def test_valid_with_hyphen(self):
        assert canasta_farmsettings.validate_extension_name("cite-4") is None

    def test_empty(self):
        assert canasta_farmsettings.validate_extension_name("") is not None

    def test_starts_with_dot(self):
        assert canasta_farmsettings.validate_extension_name(".hidden") is not None

    def test_spaces(self):
        assert canasta_farmsettings.validate_extension_name("bad name") is not None
