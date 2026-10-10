"""Tests for syncing config/wikis.yaml with wikis.yaml.template.

Editing either file and running reconcile or gitops pull
must leave the two in agreement without discarding the edit: the side that
differs from the last-applied template is the one that was edited.
"""

import os
import sys

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_gitops import (  # noqa: E402
    canasta_wikis_render_with_live_urls,
    canasta_wikis_sync_action,
)

TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
SYNC = os.path.join(TASKS, "sync_wikis_yaml.yml")
PULL = os.path.join(TASKS, "pull_compose.yml")
RECONCILE = os.path.join(
    REPO_ROOT, "roles", "instance_lifecycle", "tasks", "reconcile.yml")


def _template(name):
    return (
        "wikis:\n"
        "- id: main\n"
        "  url: {{wiki_url_main}}\n"
        "  name: %s\n" % name
    )


def _live(name, url="wiki.example.com"):
    return (
        "wikis:\n"
        "- id: main\n"
        "  url: %s\n"
        "  name: %s\n" % (url, name)
    )


class TestSyncAction:
    def test_in_agreement(self):
        assert canasta_wikis_sync_action(
            _live('"Old"'), _template('"Old"'), _template('"Old"')) == "none"

    def test_url_is_not_compared(self):
        assert canasta_wikis_sync_action(
            _live('"Old"', url="other.example.com"),
            _template('"Old"'), _template('"Old"')) == "none"

    def test_template_edit_renders(self):
        assert canasta_wikis_sync_action(
            _live('"Old"'), _template('"New"'), _template('"Old"')) == "render"

    def test_live_edit_captures(self):
        assert canasta_wikis_sync_action(
            _live('"New"'), _template('"Old"'), _template('"Old"')) == "capture"

    def test_both_edited_alike(self):
        assert canasta_wikis_sync_action(
            _live('"New"'), _template('"New"'), _template('"Old"')) == "none"

    def test_both_edited_differently_conflicts(self):
        assert canasta_wikis_sync_action(
            _live('"A"'), _template('"B"'), _template('"Old"')) == "conflict"

    def test_no_baseline_live_wins(self):
        assert canasta_wikis_sync_action(
            _live('"New"'), _template('"Old"'), "") == "capture"

    def test_quoting_differences_are_not_edits(self):
        assert canasta_wikis_sync_action(
            _live("Old"), _template('"Old"'), _template('"Old"')) == "none"

    def test_unparseable_file_is_left_alone(self):
        assert canasta_wikis_sync_action(
            "wikis: [", _template('"Old"'), _template('"Old"')) == "none"


class TestRenderWithLiveUrls:
    def test_keeps_live_url_and_template_name(self):
        out = canasta_wikis_render_with_live_urls(
            _template('"New"'), _live('"Old"'))
        wiki = yaml.safe_load(out)["wikis"][0]
        assert wiki == {
            "id": "main", "url": "wiki.example.com", "name": "New"}

    def test_list_items_stay_at_column_zero(self):
        out = canasta_wikis_render_with_live_urls(
            _template('"New"'), _live('"Old"'))
        assert "\n- id: main" in out

    def test_wiki_without_live_url_is_refused(self):
        tmpl = _template('"Main"') + (
            "- id: docs\n"
            "  url: {{wiki_url_docs}}\n"
            "  name: Docs\n")
        with pytest.raises(ValueError, match="docs"):
            canasta_wikis_render_with_live_urls(tmpl, _live('"Main"'))


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _include(t):
    return str(t.get("ansible.builtin.include_tasks")
               or t.get("include_tasks") or "")


class TestWiring:
    def test_sync_has_no_conditional_block(self):
        assert not any("block" in t for t in _load(SYNC)), (
            "sync_wikis_yaml.yml includes tasks; keep it flat")

    def test_baseline_is_the_index_and_the_template_is_staged(self):
        tasks = _load(SYNC)
        read = next(t for t in tasks
                    if t.get("name") == "Read the template as staged")
        assert read["ansible.builtin.shell"]["cmd"].startswith(
            "git show :wikis.yaml.template")
        assert tasks[-1]["ansible.builtin.command"]["cmd"] == (
            "git add -- wikis.yaml.template")

    def test_reconcile_syncs_before_regenerating_config(self):
        tasks = _load(RECONCILE)
        names = [_include(t) + str(t.get("ansible.builtin.include_role", ""))
                 for t in tasks]
        sync_i = next(i for i, n in enumerate(names)
                      if "sync_wikis_yaml.yml" in n)
        update_i = next(i for i, n in enumerate(names)
                        if "update_config.yml" in n)
        assert sync_i < update_i

    def test_pull_syncs_before_the_dirty_tree_check(self):
        tasks = _load(PULL)
        sync_i = next(i for i, t in enumerate(tasks)
                      if "sync_wikis_yaml.yml" in _include(t))
        check_i = next(i for i, t in enumerate(tasks)
                       if t.get("name") == "Fail if uncommitted changes exist")
        assert sync_i < check_i, (
            "an uncaptured config/wikis.yaml edit must be captured before "
            "the dirty-tree check, or the pull render overwrites it")
