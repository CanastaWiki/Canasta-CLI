"""Tests for the shared canasta_validate module_util.

This helper was previously exercised only transitively via the
canasta_wikis_yaml and canasta_farmsettings suites. These tests target
it directly so a change to the reserved-name list or the validation
rules is caught at the source.
"""

import canasta_validate


class TestValidateWikiId:
    def test_valid_simple(self):
        assert canasta_validate.validate_wiki_id("main") is None

    def test_valid_with_underscore(self):
        assert canasta_validate.validate_wiki_id("my_wiki") is None

    def test_valid_alphanumeric(self):
        assert canasta_validate.validate_wiki_id("wiki2") is None

    def test_valid_mixed_case(self):
        assert canasta_validate.validate_wiki_id("Wiki2") is None

    def test_space_rejected(self):
        err = canasta_validate.validate_wiki_id("my wiki")
        assert err is not None
        assert "invalid" in err

    def test_slash_rejected(self):
        err = canasta_validate.validate_wiki_id("foo/bar")
        assert err is not None
        assert "invalid" in err

    def test_dot_rejected(self):
        err = canasta_validate.validate_wiki_id("foo.bar")
        assert err is not None
        assert "invalid" in err

    def test_dotdot_rejected(self):
        err = canasta_validate.validate_wiki_id("..")
        assert err is not None
        assert "invalid" in err

    def test_unicode_rejected(self):
        err = canasta_validate.validate_wiki_id("wikí")
        assert err is not None
        assert "invalid" in err

    def test_empty_rejected(self):
        err = canasta_validate.validate_wiki_id("")
        assert err is not None
        assert "empty" in err

    def test_none_rejected(self):
        # A None value is falsy and must be rejected, not crash.
        err = canasta_validate.validate_wiki_id(None)
        assert err is not None
        assert "empty" in err

    def test_hyphen_rejected(self):
        err = canasta_validate.validate_wiki_id("my-wiki")
        assert err is not None
        assert "hyphen" in err

    def test_each_reserved_id_rejected(self):
        for reserved in canasta_validate.RESERVED_WIKI_IDS:
            err = canasta_validate.validate_wiki_id(reserved)
            assert err is not None, "%r should be reserved" % reserved
            assert "reserved" in err

    def test_reserved_list_is_exactly_expected(self):
        # Guard the canonical reserved set — these collide with paths the
        # canasta image controls (wikis/ mount, images dir, w/ and wiki/
        # URL prefixes, settings/ overrides).
        assert canasta_validate.RESERVED_WIKI_IDS == [
            "settings", "images", "w", "wiki", "wikis",
        ]
