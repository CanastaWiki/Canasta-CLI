"""Tests for scripts/wiki_publish.py page-naming helpers.

These guard against the class of bug where wiki page titles drift
from the user-facing CLI command names — e.g. 'gitops fix-submodules'
becoming 'gitops fix submodules' (spaces) and creating a duplicate
page at the wrong title.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import wiki_publish as wp


class TestDisplayName:
    def test_top_level_unchanged(self):
        assert wp.cmd_display_name("create") == "create"
        assert wp.cmd_display_name("version") == "version"

    def test_simple_subcommand_uses_space(self):
        assert wp.cmd_display_name("config_get") == "config get"
        assert wp.cmd_display_name("backup_list") == "backup list"

    def test_hyphenated_subcommand_preserves_hyphen(self):
        # The bug we shipped in #303 was that 'gitops_fix_submodules'
        # rendered as 'gitops fix submodules' (three words, all spaces),
        # producing a wiki page title different from what the user
        # types ('canasta gitops fix-submodules').
        assert (
            wp.cmd_display_name("gitops_fix_submodules")
            == "gitops fix-submodules"
        )

    def test_nested_subcommand(self):
        assert (
            wp.cmd_display_name("backup_schedule_set")
            == "backup schedule set"
        )
        assert (
            wp.cmd_display_name("storage_setup_nfs")
            == "storage setup nfs"
        )


class TestPageTitle:
    def test_uses_display_name(self):
        assert wp.cmd_page_title("create") == "CLI:canasta create"
        assert (
            wp.cmd_page_title("gitops_fix_submodules")
            == "CLI:canasta gitops fix-submodules"
        )
        assert (
            wp.cmd_page_title("backup_schedule_set")
            == "CLI:canasta backup schedule set"
        )


class TestAncestors:
    def test_top_level_has_no_ancestors(self):
        assert wp._ancestors("create") == []
        assert wp._ancestors("version") == []

    def test_subcommand_has_group_ancestor(self):
        assert wp._ancestors("config_get") == ["config"]
        assert wp._ancestors("gitops_fix_submodules") == ["gitops"]

    def test_nested_subcommand_has_full_chain(self):
        assert wp._ancestors("backup_schedule_set") == [
            "backup", "backup_schedule",
        ]
        assert wp._ancestors("storage_setup_nfs") == [
            "storage", "storage_setup",
        ]


class TestEveryCommandHasMatchingDisplayName:
    """The publisher's display-name map must cover every entry that
    canasta.py knows about as a subcommand. Drift here is the same
    class of bug as the original fix-submodules issue."""

    def test_all_subcommands_covered(self):
        missing = []
        for group, subs in wp.SUBCOMMAND_GROUPS.items():
            for sub in subs:
                internal = "%s_%s" % (group, sub.replace("-", "_"))
                expected = "%s %s" % (group, sub)
                if wp.cmd_display_name(internal) != expected:
                    missing.append("%s -> got %r, want %r" % (
                        internal, wp.cmd_display_name(internal), expected,
                    ))
        assert not missing, (
            "subcommand display-name drift:\n  " + "\n  ".join(missing)
        )

    def test_all_nested_subcommands_covered(self):
        missing = []
        for group, subgroups in wp.NESTED_SUBCOMMAND_GROUPS.items():
            for subgroup, subs in subgroups.items():
                for sub in subs:
                    internal = "%s_%s_%s" % (
                        group, subgroup, sub.replace("-", "_"),
                    )
                    expected = "%s %s %s" % (group, subgroup, sub)
                    if wp.cmd_display_name(internal) != expected:
                        missing.append("%s -> got %r, want %r" % (
                            internal, wp.cmd_display_name(internal), expected,
                        ))
        assert not missing, (
            "nested subcommand display-name drift:\n  " + "\n  ".join(missing)
        )


class TestMenuCoverage:
    """The MediaWiki:Menu-cli-reference page must link to every command,
    including 3-level nested leaves. The earlier bug was that the menu
    generator stopped at 2-level subcommands, so entries like
    'backup schedule set' and 'storage setup nfs' never appeared."""

    def _menu_content(self):
        data = wp.load_definitions()
        pages = wp.generate_all_pages(data)
        for title, content in pages:
            if title == "MediaWiki:Menu-cli-reference":
                return content
        raise AssertionError("menu page not emitted")

    def test_menu_includes_nested_leaves(self):
        menu = self._menu_content()
        expected = []
        for group, subgroups in wp.NESTED_SUBCOMMAND_GROUPS.items():
            for subgroup, subs in subgroups.items():
                for sub in subs:
                    expected.append("canasta %s %s %s" % (group, subgroup, sub))
        missing = [e for e in expected if e not in menu]
        assert not missing, (
            "menu missing nested leaves:\n  " + "\n  ".join(missing)
        )

    def test_menu_uses_five_asterisks_for_nested_leaves(self):
        menu = self._menu_content()
        # A 3-level leaf like 'backup schedule set' lives under the
        # 4-asterisk 'backup schedule' entry, so its depth marker must
        # be five asterisks — four loses the hierarchy, six orphans it.
        assert "***** " in menu
        for line in menu.splitlines():
            if line.startswith("***** "):
                assert " | canasta " in line, (
                    "malformed nested leaf line: %r" % line
                )


class TestCwdResolutionFootnote:
    """Non-required -i flags get a cwd-resolution footnote in the
    flags table. Required -i (create) does not, because there's no
    cwd resolution path when -i is required."""

    def _page_for(self, name):
        data = wp.load_definitions()
        cmd = next(c for c in data["commands"] if c["name"] == name)
        return wp.gen_wikitext(cmd)

    def test_cwd_resolvable_command_has_footnote(self):
        page = self._page_for("start")
        assert "matching the current directory" in page
        # Asterisk appears in the Default column for the -i row.
        assert "| * " in page or "|*" in page or " * |" in page

    def test_required_id_command_has_no_footnote(self):
        # create requires -i — there's no cwd resolution path, so no
        # footnote should be emitted.
        page = self._page_for("create")
        assert "matching the current directory" not in page

    def test_version_has_footnote(self):
        # After the version redesign (#324), 'canasta version'
        # cwd-resolves an instance just like every other per-instance
        # command, so the footnote is accurate for it too.
        page = self._page_for("version")
        assert "matching the current directory" in page

    def test_command_without_id_param_has_no_footnote(self):
        # doctor has no -i at all; don't accidentally emit the
        # footnote via some bug unrelated to the -i param.
        page = self._page_for("doctor")
        assert "matching the current directory" not in page


class TestGlobalFlagsSection:
    """Every command page should carry a 'Global Flags' section listing
    flags inherited by all commands (currently --help and --verbose).
    The section is rendered from data['global_flags'] in the YAML so
    there's a single source of truth for what readers see."""

    def _pages(self):
        data = wp.load_definitions()
        return dict(wp.generate_all_pages(data))

    def test_every_command_page_has_global_flags_section(self):
        pages = self._pages()
        missing = []
        for title, content in pages.items():
            if not title.startswith(wp.PAGE_PREFIX + "canasta"):
                continue
            if title == wp.PAGE_PREFIX + "canasta":
                continue  # root page has no flag table
            if "=== Global Flags ===" not in content:
                missing.append(title)
        assert not missing, (
            "pages missing Global Flags section:\n  "
            + "\n  ".join(missing)
        )

    def test_global_flags_section_lists_help_and_verbose(self):
        """The two globals today are --help (inherited from argparse)
        and --verbose. If either is missing from the rendered section
        the reader loses documentation of a real flag."""
        page = wp.gen_wikitext(
            {"name": "start", "description": "Start",
             "parameters": [{"name": "id", "short": "i",
                             "type": "string",
                             "description": "Canasta instance ID"}]},
            global_flags=wp.load_definitions()["global_flags"],
        )
        assert "=== Global Flags ===" in page
        assert "--help" in page
        assert "--verbose" in page

    def test_global_flags_omitted_when_not_supplied(self):
        """gen_wikitext called without global_flags (e.g. from tests)
        emits the page without the Global Flags section — back-compat."""
        page = wp.gen_wikitext(
            {"name": "start", "description": "Start",
             "parameters": [{"name": "id", "short": "i",
                             "type": "string",
                             "description": "Canasta instance ID"}]}
        )
        assert "=== Global Flags ===" not in page


class TestOrchestratorColumn:
    """Flag tables carry an 'Orchestrator' column so readers can see at
    a glance which orchestrator each flag applies to. Values come from
    the YAML's optional `orchestrator_only` field: unset → 'Both',
    'kubernetes' or 'k8s' → 'Kubernetes', 'compose' → 'Compose'."""

    def _create_page(self):
        data = wp.load_definitions()
        cmd = next(c for c in data["commands"] if c["name"] == "create")
        return wp.gen_wikitext(cmd, global_flags=data["global_flags"])

    def test_column_header_present(self):
        page = self._create_page()
        assert "Orchestrator" in page

    def test_kubernetes_only_param_labelled_kubernetes(self):
        page = self._create_page()
        # --storage-class has orchestrator_only: kubernetes. Its row
        # must include the 'Kubernetes' label.
        for line in page.splitlines():
            if "<code>--storage-class</code>" in line:
                assert "Kubernetes" in line
                return
        raise AssertionError("--storage-class row not found")

    def test_compose_only_param_labelled_compose(self):
        page = self._create_page()
        for line in page.splitlines():
            if "<code>--override</code>" in line:
                assert "Compose" in line
                return
        raise AssertionError("--override row not found")

    def test_orchestrator_neutral_param_labelled_both(self):
        page = self._create_page()
        # --id is the plain per-instance flag, applies to both.
        for line in page.splitlines():
            if "<code>--id</code>" in line:
                assert "Both" in line
                return
        raise AssertionError("--id row not found")

    def test_global_flags_section_also_has_column(self):
        """Global flags apply to every command regardless of
        orchestrator, so they show 'Both' in the column."""
        page = self._create_page()
        # The Global Flags section is the tail of the page after
        # '=== Global Flags ==='.
        gf = page.split("=== Global Flags ===", 1)[1]
        assert "Orchestrator" in gf
        for line in gf.splitlines():
            if "<code>--help</code>" in line or "<code>--verbose</code>" in line:
                assert "Both" in line

    def test_label_helper_maps_values(self):
        assert wp._orchestrator_label(None) == "Both"
        assert wp._orchestrator_label("") == "Both"
        assert wp._orchestrator_label("kubernetes") == "Kubernetes"
        assert wp._orchestrator_label("k8s") == "Kubernetes"
        assert wp._orchestrator_label("compose") == "Compose"


class TestUsageLineSkipsWhenNoParams:
    """A command with no parameters has no flags to document in the
    usage line. Emitting 'canasta host list' as a synoptic block
    duplicates the bare-command example below. Skip the block entirely
    in that case."""

    def test_command_with_no_params_omits_usage_block(self):
        page = wp.gen_wikitext({
            "name": "host_list",
            "description": "List hosts",
            "long_description": "List every saved host definition.",
            "examples": ["canasta host list"],
        })
        # The '[flags]' placeholder must not appear at all when there
        # are no flags.
        assert "[flags]" not in page
        # There should be exactly one occurrence of a syntaxhighlight
        # block (the Examples block), not two.
        assert page.count('<syntaxhighlight lang="bash"') == 1

    def test_command_with_params_keeps_usage_block(self):
        page = wp.gen_wikitext({
            "name": "start",
            "description": "Start",
            "long_description": "Start the instance.",
            "examples": ["canasta start"],
            "parameters": [{"name": "id", "short": "i",
                            "type": "string",
                            "description": "Canasta instance ID"}],
        })
        # With parameters, '[flags]' must appear in the usage block.
        assert "canasta start [flags]" in page
        # Two syntaxhighlight blocks: one for usage, one for examples.
        assert page.count('<syntaxhighlight lang="bash"') == 2


class TestBacktickToCode:
    """MediaWiki doesn't interpret single-backtick inline code (unlike
    markdown). The publisher translates backticks to <code> tags so
    readers see inline code on the rendered page, not literal
    backticks."""

    def test_simple(self):
        assert wp._backticks_to_code("Use `canasta start`.") == (
            "Use <code>canasta start</code>."
        )

    def test_multiple_spans(self):
        assert wp._backticks_to_code("`a` and `b`") == (
            "<code>a</code> and <code>b</code>"
        )

    def test_empty_and_none(self):
        assert wp._backticks_to_code("") == ""
        assert wp._backticks_to_code(None) is None

    def test_unpaired_backtick_preserved(self):
        # Shouldn't swallow an orphan backtick — leave text as-is.
        assert wp._backticks_to_code("just ` a stray") == "just ` a stray"

    def test_no_span_across_newlines(self):
        # Rare in practice, but guard the regex: a backtick that opens
        # on one line shouldn't close on the next.
        assert wp._backticks_to_code("line one `\nline two`") == (
            "line one `\nline two`"
        )

    def test_renders_in_command_page(self):
        """End-to-end check: long_description with backticks lands in
        the generated page as <code> tags, not as literal backticks."""
        page = wp.gen_wikitext({
            "name": "start",
            "description": "Start",
            "long_description": "Use `canasta start` to boot the stack.",
            "parameters": [{"name": "id", "short": "i",
                            "type": "string",
                            "description": "Canasta instance ID"}],
        })
        assert "<code>canasta start</code>" in page
        # And the literal backtick-wrapped form should not appear.
        assert "`canasta start`" not in page
