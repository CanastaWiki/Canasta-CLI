"""Tests for scripts/validate_wiki_examples.py.

The validator's job is to fail on real drift without crying wolf, so
these cover both directions: the drift classes seen in practice, and the
wiki constructs that look like drift but are not (passthrough args,
placeholders, prose, non-shell blocks).
"""

import os
import sys

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts"),
)
sys.path.insert(0, SCRIPTS_DIR)

import validate_wiki_examples as v  # noqa: E402


DEFINITIONS = {
    "global_flags": [
        {"name": "help", "short": "h"},
        {"name": "verbose", "short": "v"},
    ],
    "command_groups": [
        {"name": "backup"}, {"name": "backup_schedule"},
        {"name": "storage"}, {"name": "storage_setup"},
        {"name": "host"}, {"name": "maintenance"}, {"name": "gitops"},
    ],
    "commands": [
        {"name": "restart", "parameters": [{"name": "id", "short": "i",
                                            "type": "string"}]},
        {"name": "create", "parameters": [
            {"name": "id", "short": "i", "type": "string"},
            {"name": "wiki", "short": "w", "type": "string"},
        ]},
        {"name": "backup_purge", "parameters": [
            {"name": "keep_within", "type": "string"},
        ]},
        {"name": "backup_schedule_set", "parameters": [
            {"name": "purge_older_than", "type": "string"},
        ]},
        {"name": "storage_setup_nfs", "parameters": [
            {"name": "host", "short": "H", "type": "string"},
            {"name": "install_server", "type": "bool"},
        ]},
        # `long:` renames the flag — --name, not --host-name.
        {"name": "host_add", "parameters": [
            {"name": "host_name", "long": "name", "short": "n",
             "type": "string"},
        ]},
        {"name": "maintenance_script", "parameters": [
            {"name": "id", "short": "i", "type": "string"},
            {"name": "script_args", "positional": True},
        ]},
        {"name": "gitops_status", "parameters": [
            {"name": "id", "short": "i", "type": "string"},
        ]},
    ],
}

TABLE = v.build_command_table(DEFINITIONS)

# The stub above could drift from the real schema; a few tests run the
# same code against the shipped definitions.
REAL_DEFINITIONS = v.load_definitions()
REAL_TABLE = v.build_command_table(REAL_DEFINITIONS)


def check(line):
    """Validate one example line; return the finding detail or None."""
    tokens = v.tokenize(line)
    if not tokens:
        return None
    problem = v.validate_example(tokens, TABLE, DEFINITIONS)
    return None if problem is None else " ".join(problem)


def block(body, lang="bash"):
    return '<syntaxhighlight lang="%s" copy=1>\n%s\n</syntaxhighlight>' % (
        lang, body,
    )


class TestCommandResolution:
    def test_plain_command(self):
        assert check("canasta restart -i mywiki") is None

    def test_two_token_group_command(self):
        assert check("canasta backup purge --keep-within 30d") is None

    def test_three_token_group_command(self):
        """`backup_schedule_set` splits into three CLI tokens, and the
        group name itself contains an underscore."""
        assert check('canasta backup schedule set "0 2 * * *"') is None

    def test_group_with_underscore_in_its_name(self):
        assert check(
            "canasta storage setup nfs --host node1 --install-server"
        ) is None

    def test_unknown_command_is_reported(self):
        assert "unknown command" in check("canasta frobnicate -i x")

    def test_renamed_command_is_reported(self):
        """A command that used to exist under another name."""
        assert "unknown command" in check("canasta crowdsec enroll")


class TestFlagValidation:
    def test_unknown_long_flag(self):
        assert check("canasta restart --host node1 -i x") == (
            "unknown flag: --host"
        )

    def test_unknown_short_flag(self):
        assert check("canasta restart -Z") == "unknown flag: -Z"

    def test_nonexistent_flag_on_a_destructive_command(self):
        """The `backup purge --dry-run` class: a documented safety flag
        that does not exist, where the recovery is to drop it and run
        the destructive command for real."""
        assert check("canasta backup purge --keep-within 30d --dry-run") == (
            "unknown flag: --dry-run"
        )

    def test_long_override_is_honored(self):
        """host_add declares `long: name`, so --name is correct and
        --host-name is not."""
        assert check("canasta host add --name prod1") is None
        assert "unknown flag" in check("canasta host add --host-name prod1")

    def test_flag_with_inline_value(self):
        assert check("canasta create --id=mywiki") is None
        assert check("canasta create --nope=x") == "unknown flag: --nope"

    def test_global_flags_are_accepted(self):
        assert check("canasta restart --verbose") is None
        assert check("canasta restart -h") is None

    def test_negative_number_is_not_a_flag(self):
        assert check("canasta backup purge --keep-within -1") is None


class TestPassthroughArguments:
    """Flags after a passthrough positional belong to another program."""

    def test_script_flags_are_not_ours(self):
        assert check(
            "canasta maintenance script createAndPromote.php U --bureaucrat"
        ) is None

    def test_our_flags_before_the_script_still_checked(self):
        assert check(
            "canasta maintenance script -Q foo.php --bureaucrat"
        ) == "unknown flag: -Q"

    def test_double_dash_stops_validation(self):
        assert check("canasta restart -i x -- php -m") is None


class TestLineExtraction:
    def test_only_shell_blocks_are_scanned(self):
        """A yaml/php block can contain anything; prose mentions of a
        command in <code> tags are not runnable examples."""
        text = "\n".join([
            block("canasta restart --host x"),
            block("canasta: --host x", lang="yaml"),
            "Run <code>canasta restart --host x</code> to restart.",
        ])
        assert len(list(v.iter_shell_lines(text))) == 1

    def test_backslash_continuations_are_joined(self):
        """A flag alone on a continuation line is part of the command
        above it — checking only the first line misses it entirely."""
        text = block(
            "canasta storage setup nfs \\\n"
            "  --host node1 \\\n"
            "  --bogus-flag"
        )
        lines = list(v.iter_shell_lines(text))
        assert len(lines) == 1
        assert check(lines[0][1]) == "unknown flag: --bogus-flag"

    def test_line_numbers_point_at_the_command(self):
        text = "intro\n\n" + block("echo hi\ncanasta restart -i x")
        (line_no, _), = list(v.iter_shell_lines(text))
        assert text.split("\n")[line_no - 1].startswith("canasta restart")

    def test_prompts_and_list_markers(self):
        assert list(v.iter_shell_lines(block("$ canasta restart -i x")))

    def test_non_canasta_lines_ignored(self):
        text = block("kubectl get pods\ndocker compose ps")
        assert list(v.iter_shell_lines(text)) == []

    def test_wrapper_names_are_recognized(self):
        text = block("canasta-native restart -i x\ncanasta-docker restart -i x")
        assert len(list(v.iter_shell_lines(text))) == 2


class TestTokenizing:
    def test_placeholders_do_not_break_parsing(self):
        assert check("canasta restart -i <your-instance>") is None

    def test_trailing_comment_is_stripped(self):
        assert check("canasta restart -i x   # restart it") is None

    def test_only_the_first_command_of_a_pipeline(self):
        assert check("canasta restart -i x | grep ok") is None

    def test_unparseable_line_is_skipped_not_crashed(self):
        assert v.tokenize("canasta restart -i 'unbalanced") is None

    def test_bare_command_name(self):
        assert check("canasta restart") is None


class TestEndToEnd:
    def test_clean_page_produces_no_findings(self):
        text = block("canasta restart -i mywiki\ncanasta backup purge "
                     "--keep-within 30d")
        assert v.validate_page("Help:X", text, TABLE, DEFINITIONS) == []

    def test_findings_carry_page_line_and_example(self):
        text = "lead\n" + block("canasta restart --host node1")
        finding, = v.validate_page("Help:X", text, TABLE, DEFINITIONS)
        assert finding.page == "Help:X"
        assert "--host" in finding.detail
        assert "canasta restart --host node1" in finding.example
        assert "Help:X" in str(finding)


class TestAgainstRealDefinitions:
    """The stub table above could drift from the real schema; these run
    the same code against meta/command_definitions.yml."""

    def test_every_command_is_reachable(self):
        definitions, table = REAL_DEFINITIONS, REAL_TABLE
        assert len(table) >= len(definitions["commands"])

    def test_documented_examples_all_validate(self):
        """Each command's own `examples:` must pass — they are the
        canonical usage, so a failure means the validator is wrong."""
        definitions, table = REAL_DEFINITIONS, REAL_TABLE
        for command in definitions["commands"]:
            for example in command.get("examples") or []:
                tokens = v.tokenize(example)
                assert tokens, example
                problem = v.validate_example(tokens, table, definitions)
                assert problem is None, "%s -> %s" % (example, problem)
