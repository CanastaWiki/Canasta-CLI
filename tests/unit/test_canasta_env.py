"""Tests for the canasta_env Ansible module."""


from unittest.mock import patch

import pytest

import canasta_env
from mock_ansible import MockAnsibleModule, run_module_with_params


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

    def test_value_with_line_break_is_refused(self, sample_env_file):
        with open(sample_env_file) as f:
            before = f.read()
        for value in ("x\nMW_SECRET_KEY=evil", "x\rY=1", "x\r\n"):
            result, failed, _ = run_module_with_params(canasta_env, {
                "path": sample_env_file, "state": "set",
                "key": "NEW_KEY", "value": value, "keys": None,
            })
            assert failed
            assert "single line" in result["msg"]
            assert "evil" not in result["msg"]
        with open(sample_env_file) as f:
            assert f.read() == before


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


class TestRawLineHelpers:
    def test_raw_value_keeps_quotes(self):
        assert canasta_env.raw_value_of('K="secret"') == '"secret"'
        assert canasta_env.raw_value_of("K='s'") == "'s'"
        assert canasta_env.raw_value_of("K=bare") == "bare"

    def test_raw_value_none_for_non_entry(self):
        assert canasta_env.raw_value_of("# comment") is None
        assert canasta_env.raw_value_of("") is None
        assert canasta_env.raw_value_of("noequals") is None

    def test_set_line_normalizes_quoted_target(self):
        lines = ['A="secret"', "B=keep"]
        out = canasta_env.set_line(lines, "A", "secret")
        assert out == ["A=secret", "B=keep"]

    def test_set_line_leaves_other_lines_verbatim(self):
        lines = ['A="a #b"', "B=old"]
        out = canasta_env.set_line(lines, "B", "new")
        # A untouched byte-for-byte, including the inline '#' inside quotes.
        assert out == ['A="a #b"', "B=new"]

    def test_set_line_dedupes_and_appends(self):
        assert canasta_env.set_line(["K=1", "K=2", "X=y"], "K", "3") == [
            "K=3", "X=y",
        ]
        assert canasta_env.set_line(["A=1"], "B", "2") == ["A=1", "B=2"]

    def test_set_line_refuses_line_breaks(self):
        with pytest.raises(ValueError, match="single line"):
            canasta_env.set_line(["A=1"], "B", "2\nC=3")

    def test_unset_lines_keeps_others_verbatim(self):
        lines = ['A="a #b"', "B=1", "# c", "B=2"]
        assert canasta_env.unset_lines(lines, "B") == ['A="a #b"', "# c"]


class TestSetQuoteHandling:
    def _write(self, tmp_path, content):
        p = tmp_path / ".env"
        p.write_text(content)
        return str(p)

    def test_quote_only_change_reports_changed_and_repairs(self, tmp_path):
        # Value equal after stripping the existing quotes must still be
        # detected as a change so the quotes get repaired.
        path = self._write(tmp_path, 'RESTIC_PASSWORD="secret"\nOTHER=x\n')
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": path, "state": "set",
            "key": "RESTIC_PASSWORD", "value": "secret", "keys": None,
        })
        assert not failed
        assert result["changed"]
        assert open(path).read() == "RESTIC_PASSWORD=secret\nOTHER=x\n"

    def test_already_clean_value_is_idempotent(self, tmp_path):
        path = self._write(tmp_path, "K=v\n")
        result, _f, _ = run_module_with_params(canasta_env, {
            "path": path, "state": "set", "key": "K", "value": "v",
            "keys": None,
        })
        assert not result["changed"]
        assert open(path).read() == "K=v\n"

    def test_setting_key_leaves_other_quoted_value_intact(self, tmp_path):
        # Setting B must not touch A, whose quoted value contains ' #'
        # (which compose's env parser would truncate if unquoted).
        original = 'A="abc #def"\nB=old\n# keep\n'
        path = self._write(tmp_path, original)
        result, _f, _ = run_module_with_params(canasta_env, {
            "path": path, "state": "set", "key": "B", "value": "new",
            "keys": None,
        })
        assert result["changed"]
        assert open(path).read() == 'A="abc #def"\nB=new\n# keep\n'

    def test_new_key_append_keeps_existing_quotes(self, tmp_path):
        path = self._write(tmp_path, 'A="q #x"\n')
        run_module_with_params(canasta_env, {
            "path": path, "state": "set", "key": "NEW", "value": "1",
            "keys": None,
        })
        assert open(path).read() == 'A="q #x"\nNEW=1\n'

    def test_unset_keeps_other_quoted_values_intact(self, tmp_path):
        path = self._write(tmp_path, 'A="q #x"\nB=drop\n')
        result, _f, _ = run_module_with_params(canasta_env, {
            "path": path, "state": "unset", "key": "B", "value": None,
            "keys": None,
        })
        assert result["changed"]
        assert open(path).read() == 'A="q #x"\n'


class TestArgumentSpec:
    def test_value_is_no_log(self, tmp_path):
        # Values such as passwords must be masked in module invocation
        # logs on the target.
        mock = MockAnsibleModule({
            "path": str(tmp_path / ".env"), "state": "read_all",
            "key": None, "value": None, "keys": None,
        })
        with patch.object(canasta_env, "AnsibleModule",
                          return_value=mock) as ctor:
            try:
                canasta_env.run_module()
            except SystemExit:
                pass
        spec = ctor.call_args.kwargs["argument_spec"]
        assert spec["value"].get("no_log") is True


class TestFinalNewline:
    """Every .env the module writes ends with a newline, so a setting an
    operator appends with `echo KEY=value >> .env` lands on its own line."""

    def _set(self, path, key, value):
        result, failed, msg = run_module_with_params(canasta_env, {
            "path": path, "state": "set",
            "key": key, "value": value, "keys": None,
        })
        assert not failed, msg
        return result

    def _keys(self, path):
        content = open(path).read()
        return canasta_env.entries_to_dict(canasta_env.parse_env_file(content))

    def test_set_line_appends_before_final_newline(self):
        assert canasta_env.set_line(["A=1", ""], "B", "2") == [
            "A=1", "B=2", "",
        ]
        assert canasta_env.set_line([""], "B", "2") == ["B=2", ""]

    def test_lines_to_content_ends_with_newline(self):
        assert canasta_env.lines_to_content(["A=1", "B=2"]) == "A=1\nB=2\n"
        assert canasta_env.lines_to_content(["A=1", ""]) == "A=1\n"
        assert canasta_env.lines_to_content([""]) == ""
        assert canasta_env.lines_to_content([]) == ""

    def test_create_then_gitops_init_then_echo_append(self, tmp_path):
        # create copies a .env that ends with a newline, then sets keys it
        # does not have (CANASTA_CADDY_HTTPS_PUBLISH last); gitops init
        # leaves .env as it finds it.
        p = tmp_path / ".env"
        p.write_text("# Canasta\nMW_SITE_SERVER=https://example.com\n")
        path = str(p)
        self._set(path, "MW_SITE_SERVER", "https://wiki.example.com")
        self._set(path, "COMPOSE_PROFILES", "varnish")
        self._set(path, "CANASTA_CADDY_HTTPS_PUBLISH", "443:443")
        assert p.read_text() == (
            "# Canasta\n"
            "MW_SITE_SERVER=https://wiki.example.com\n"
            "COMPOSE_PROFILES=varnish\n"
            "CANASTA_CADDY_HTTPS_PUBLISH=443:443\n"
        )
        with open(path, "a") as f:
            f.write("E2E_SYNC_TEST=1\n")
        keys = self._keys(path)
        assert keys["E2E_SYNC_TEST"] == "1"
        assert keys["CANASTA_CADDY_HTTPS_PUBLISH"] == "443:443"

    def test_write_repairs_file_without_final_newline(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("A=1\n\nB=2")
        self._set(str(p), "B", "3")
        assert p.read_text() == "A=1\n\nB=3\n"
        p.write_text("A=1\n\nB=2")
        self._set(str(p), "C", "3")
        assert p.read_text() == "A=1\n\nB=2\nC=3\n"

    def test_unset_ends_with_newline(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("A=1\nB=2")
        result, failed, msg = run_module_with_params(canasta_env, {
            "path": str(p), "state": "unset",
            "key": "B", "value": None, "keys": None,
        })
        assert not failed, msg
        assert p.read_text() == "A=1\n"
