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
