"""Tests for the canasta_env Ansible module."""


import canasta_env
from mock_ansible import run_module_with_params


class TestParseEnvFile:
    def test_basic_parsing(self):
        content = "KEY1=value1\nKEY2=value2\n"
        entries = canasta_env.parse_env_file(content)
        d = canasta_env.entries_to_dict(entries)
        assert d == {"KEY1": "value1", "KEY2": "value2"}

    def test_comments_preserved(self):
        content = "# comment\nKEY=val\n"
        entries = canasta_env.parse_env_file(content)
        assert entries[0] == (None, "# comment", True)
        assert entries[1] == ("KEY", "val", False)

    def test_blank_lines_preserved(self):
        content = "KEY1=val1\n\nKEY2=val2\n"
        entries = canasta_env.parse_env_file(content)
        assert entries[1] == (None, "", True)

    def test_quoted_values_stripped(self):
        content = 'KEY="hello world"\n'
        entries = canasta_env.parse_env_file(content)
        d = canasta_env.entries_to_dict(entries)
        assert d["KEY"] == "hello world"

    def test_single_quoted_values_stripped(self):
        content = "KEY='hello world'\n"
        entries = canasta_env.parse_env_file(content)
        d = canasta_env.entries_to_dict(entries)
        assert d["KEY"] == "hello world"

    def test_equals_in_value(self):
        content = "KEY=a=b=c\n"
        entries = canasta_env.parse_env_file(content)
        d = canasta_env.entries_to_dict(entries)
        assert d["KEY"] == "a=b=c"

    def test_empty_value(self):
        content = "KEY=\n"
        entries = canasta_env.parse_env_file(content)
        d = canasta_env.entries_to_dict(entries)
        assert d["KEY"] == ""

    def test_duplicate_keys_last_wins(self):
        content = "KEY=first\nKEY=second\n"
        entries = canasta_env.parse_env_file(content)
        d = canasta_env.entries_to_dict(entries)
        assert d["KEY"] == "second"


class TestRoundTrip:
    def test_roundtrip_preserves_format(self):
        content = "# comment\nKEY1=val1\n\nKEY2=val2"
        entries = canasta_env.parse_env_file(content)
        result = canasta_env.entries_to_content(entries)
        assert result == content


class TestSetVariable:
    def test_update_existing(self):
        entries = canasta_env.parse_env_file("KEY=old\n")
        entries = canasta_env.set_variable(entries, "KEY", "new")
        d = canasta_env.entries_to_dict(entries)
        assert d["KEY"] == "new"

    def test_append_new(self):
        entries = canasta_env.parse_env_file("KEY1=val1\n")
        entries = canasta_env.set_variable(entries, "KEY2", "val2")
        d = canasta_env.entries_to_dict(entries)
        assert d["KEY2"] == "val2"

    def test_deduplicates(self):
        entries = canasta_env.parse_env_file("KEY=a\nKEY=b\n")
        entries = canasta_env.set_variable(entries, "KEY", "c")
        content = canasta_env.entries_to_content(entries)
        assert content.count("KEY=") == 1

    def test_preserves_comments(self):
        entries = canasta_env.parse_env_file("# header\nKEY=old\n# footer\n")
        entries = canasta_env.set_variable(entries, "KEY", "new")
        content = canasta_env.entries_to_content(entries)
        assert "# header" in content
        assert "# footer" in content


class TestUnsetVariable:
    def test_removes_key(self):
        entries = canasta_env.parse_env_file("KEY1=a\nKEY2=b\n")
        entries = canasta_env.unset_variable(entries, "KEY1")
        d = canasta_env.entries_to_dict(entries)
        assert "KEY1" not in d
        assert "KEY2" in d

    def test_removes_all_duplicates(self):
        entries = canasta_env.parse_env_file("KEY=a\nKEY=b\n")
        entries = canasta_env.unset_variable(entries, "KEY")
        d = canasta_env.entries_to_dict(entries)
        assert "KEY" not in d

    def test_preserves_comments(self):
        entries = canasta_env.parse_env_file("# keep\nKEY=val\n")
        entries = canasta_env.unset_variable(entries, "KEY")
        content = canasta_env.entries_to_content(entries)
        assert "# keep" in content

    def test_nonexistent_key_noop(self):
        entries = canasta_env.parse_env_file("KEY=val\n")
        before = canasta_env.entries_to_content(entries)
        entries = canasta_env.unset_variable(entries, "NOPE")
        after = canasta_env.entries_to_content(entries)
        assert before == after


class TestFileOperations:
    def test_set_writes_file(self, sample_env_file):
        entries = canasta_env.parse_env_file(open(sample_env_file).read())
        entries = canasta_env.set_variable(entries, "NEW_KEY", "new_value")
        content = canasta_env.entries_to_content(entries)
        with open(sample_env_file, "w") as f:
            f.write(content)
        # Re-read and verify
        with open(sample_env_file) as f:
            d = canasta_env.entries_to_dict(canasta_env.parse_env_file(f.read()))
        assert d["NEW_KEY"] == "new_value"
        assert d["MW_SITE_SERVER"] == "https://example.com"


class TestRunModuleReadAll:
    def test_read_all(self, sample_env_file):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "read_all",
            "key": None, "value": None, "keys": None,
        })
        assert not failed
        assert result["variables"]["MW_SITE_SERVER"] == "https://example.com"
        assert result["variables"]["QUOTED_VALUE"] == "hello world"


class TestRunModuleRead:
    def test_read_existing_key(self, sample_env_file):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "read",
            "key": "MW_SITE_SERVER", "value": None, "keys": None,
        })
        assert not failed
        assert result["found"]
        assert result["value"] == "https://example.com"

    def test_read_missing_key(self, sample_env_file):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "read",
            "key": "NONEXISTENT", "value": None, "keys": None,
        })
        assert not failed
        assert not result["found"]


class TestRunModuleSet:
    def test_set_new_key(self, sample_env_file):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "set",
            "key": "NEW_KEY", "value": "new_value", "keys": None,
        })
        assert not failed
        assert result["changed"]

    def test_set_same_value_no_change(self, sample_env_file):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "set",
            "key": "MW_SITE_SERVER", "value": "https://example.com", "keys": None,
        })
        assert not failed
        assert not result["changed"]


class TestRunModuleUnset:
    def test_unset_existing(self, sample_env_file):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "unset",
            "key": "MW_SITE_SERVER", "value": None, "keys": None,
        })
        assert not failed
        assert result["changed"]

    def test_unset_nonexistent(self, sample_env_file):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "unset",
            "key": "NONEXISTENT", "value": None, "keys": None,
        })
        assert not failed
        assert not result["changed"]


class TestLintEnvFile:
    def test_clean_file_has_no_issues(self):
        assert canasta_env.lint_env_file("A=1\nB=2\n# c\n") == ([], False)

    def test_double_quoted_value_flagged(self):
        assert canasta_env.lint_env_file('PW="secret"\n') == (["PW"], False)

    def test_single_quoted_value_flagged(self):
        assert canasta_env.lint_env_file("PW='secret'\n") == (["PW"], False)

    def test_unquoted_value_not_flagged(self):
        assert canasta_env.lint_env_file("PW=secret\n") == ([], False)

    def test_crlf_detected(self):
        quoted, crlf = canasta_env.lint_env_file("A=1\r\nB=2\r\n")
        assert quoted == [] and crlf is True

    def test_quoted_and_crlf_together(self):
        # The trailing CR must not defeat quote detection.
        quoted, crlf = canasta_env.lint_env_file('PW="x"\r\nA=1\r\n')
        assert quoted == ["PW"] and crlf is True

    def test_comments_and_blanks_ignored(self):
        assert canasta_env.lint_env_file('# PW="x"\n\nA=1\n') == ([], False)

    def test_multiple_quoted_keys(self):
        assert canasta_env.lint_env_file('A="1"\nB=2\nC=\'3\'\n') == (
            ["A", "C"], False
        )


class TestRunModuleLint:
    def test_lint_flags_quoted_values(self, sample_env_file):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "lint",
            "key": None, "value": None, "keys": None,
        })
        assert not failed
        assert "QUOTED_VALUE" in result["quoted_keys"]
        assert "MW_SITE_NAME" in result["quoted_keys"]
        assert result["has_crlf"] is False
