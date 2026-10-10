"""Tests for syncing .env with its gitops sources.

A direct .env edit must reach env.template / the host's vars.yaml before
anything re-renders .env from them, and an edit to the sources must not be
mistaken for one: the side that differs from the last-applied render is the
one that was edited.
"""

import os
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_gitops import (  # noqa: E402
    canasta_env_render as render,
    canasta_env_sync_plan as plan,
)

TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
SYNC = os.path.join(TASKS, "sync_env.yml")
PULL = os.path.join(TASKS, "pull_compose.yml")
RECONCILE = os.path.join(REPO_ROOT, "roles", "instance_lifecycle", "tasks",
                         "reconcile.yml")

TEMPLATE = (
    "# Canasta\n"
    "MW_SITE_SERVER={{mw_site_server}}\n"
    "HTTP_PORT=80\n"
    "MYSQL_PASSWORD={{mysql_password}}\n"
)
VARS = {"mw_site_server": "https://example.com", "mysql_password": "pw"}
LIVE = (
    "MW_SITE_SERVER=https://example.com\n"
    "HTTP_PORT=80\n"
    "MYSQL_PASSWORD=pw\n"
    "COMPOSE_PROFILES=varnish,internal-db\n"
)


def _edit(text, key, value):
    out = []
    for line in text.splitlines():
        if line.startswith(key + "="):
            if value is None:
                continue
            line = "%s=%s" % (key, value)
        out.append(line)
    return "\n".join(out) + "\n"


class TestRender:
    def test_placeholders_literals_and_comments(self):
        assert render(TEMPLATE, VARS) == (
            "# Canasta\n"
            "MW_SITE_SERVER=https://example.com\n"
            "HTTP_PORT=80\n"
            "MYSQL_PASSWORD=pw\n"
            "\n"
        )

    def test_placeholder_without_a_value_is_left_out(self):
        out = render("A={{a}}\nB={{b}}\nC={{c}}\n",
                     {"a": "", "b": None, "c": 0})
        assert "A=" not in out and "B=" not in out
        assert "C=0\n" in out


class TestPlan:
    def test_in_agreement(self):
        assert plan(LIVE, TEMPLATE, VARS, TEMPLATE, VARS) == {
            "set": {}, "unset": [], "conflict": []}

    def test_compose_profiles_is_not_compared(self):
        live = _edit(LIVE, "COMPOSE_PROFILES", "varnish")
        assert plan(live, TEMPLATE, VARS, TEMPLATE, VARS)["set"] == {}

    def test_live_edit_is_written_to_the_sources(self):
        live = _edit(LIVE, "HTTP_PORT", "8080")
        assert plan(live, TEMPLATE, VARS, TEMPLATE, VARS)["set"] == {
            "HTTP_PORT": "8080"}

    def test_key_added_to_live_is_written_to_the_sources(self):
        live = LIVE + "SMTP_HOST=mail.example.com\n"
        assert plan(live, TEMPLATE, VARS, TEMPLATE, VARS)["set"] == {
            "SMTP_HOST": "mail.example.com"}

    def test_key_deleted_from_live_is_removed_from_the_sources(self):
        live = _edit(LIVE, "HTTP_PORT", None)
        assert plan(live, TEMPLATE, VARS, TEMPLATE, VARS)["unset"] == [
            "HTTP_PORT"]

    def test_source_edit_is_left_for_the_render(self):
        new_vars = dict(VARS, mysql_password="new")
        assert plan(LIVE, TEMPLATE, new_vars, TEMPLATE, VARS) == {
            "set": {}, "unset": [], "conflict": []}
        new_template = TEMPLATE.replace("HTTP_PORT=80", "HTTP_PORT=81")
        assert plan(LIVE, new_template, VARS, TEMPLATE, VARS) == {
            "set": {}, "unset": [], "conflict": []}

    def test_same_edit_on_both_sides(self):
        live = _edit(LIVE, "HTTP_PORT", "81")
        new_template = TEMPLATE.replace("HTTP_PORT=80", "HTTP_PORT=81")
        assert plan(live, new_template, VARS, TEMPLATE, VARS) == {
            "set": {}, "unset": [], "conflict": []}

    def test_different_edits_on_both_sides_conflict(self):
        live = _edit(LIVE, "MYSQL_PASSWORD", "mine")
        new_vars = dict(VARS, mysql_password="theirs")
        result = plan(live, TEMPLATE, new_vars, TEMPLATE, VARS)
        assert result["conflict"] == ["MYSQL_PASSWORD"]
        assert result["set"] == {}

    def test_edits_to_different_keys_do_not_conflict(self):
        live = _edit(LIVE, "HTTP_PORT", "8080")
        new_vars = dict(VARS, mysql_password="new")
        assert plan(live, TEMPLATE, new_vars, TEMPLATE, VARS) == {
            "set": {"HTTP_PORT": "8080"}, "unset": [], "conflict": []}

    def test_no_baseline_live_wins(self):
        live = _edit(LIVE, "HTTP_PORT", "8080")
        new_template = TEMPLATE.replace("HTTP_PORT=80", "HTTP_PORT=81")
        assert plan(live, new_template, VARS, "", {})["set"] == {
            "HTTP_PORT": "8080"}


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _include(t):
    return str(t.get("ansible.builtin.include_tasks")
               or t.get("include_tasks") or "")


class TestWiring:
    def test_sync_has_no_conditional_block(self):
        assert not any("block" in t for t in _load(SYNC))

    def test_sync_writes_through_the_config_set_tasks(self):
        includes = [_include(t) for t in _load(SYNC)]
        assert any("_update_gitops_vars.yml" in i for i in includes)
        assert any("_remove_gitops_vars.yml" in i for i in includes)

    def test_values_are_never_logged(self):
        for t in _load(SYNC):
            if t.get("name") in ("Read the .env sources now and as staged",
                                 "Read the live .env for the sync",
                                 "Compare .env with its sources"):
                assert t.get("no_log") is True, t["name"]

    def test_baseline_is_the_index_and_sources_are_staged(self):
        # The sources are staged whenever .env and they agree, so the index
        # is the last agreement; a commit would make a second edit of a
        # saved-but-unpushed key look like a conflict.
        tasks = _load(SYNC)
        read = next(t for t in tasks
                    if t.get("name") == "Read the .env sources now and as staged")
        assert 'git cat-file --filters ":$f"' in read["ansible.builtin.shell"]["cmd"]
        assert "gitops-applied" not in read["ansible.builtin.shell"]["cmd"]
        assert tasks[-1]["name"] == "Stage the .env sources"
        assert "git add" in tasks[-1]["ansible.builtin.shell"]["cmd"]

    def test_reconcile_syncs_then_renders_then_derives_profiles(self):
        includes = [_include(t) for t in _load(RECONCILE)]
        env_i = next(i for i, x in enumerate(includes) if "sync_env.yml" in x)
        render_i = next(i for i, x in enumerate(includes)
                        if "render_gitops_config.yml" in x)
        profiles_i = next(i for i, x in enumerate(includes)
                          if "sync_compose_profiles.yml" in x)
        assert env_i < render_i < profiles_i

    def test_pull_syncs_before_the_dirty_tree_check(self):
        tasks = _load(PULL)
        sync_i = next(i for i, t in enumerate(tasks)
                      if "sync_env.yml" in _include(t))
        check_i = next(i for i, t in enumerate(tasks)
                       if t.get("name") == "Fail if uncommitted changes exist")
        assert sync_i < check_i

    def test_config_regenerate_is_gone(self):
        assert not os.path.exists(
            os.path.join(REPO_ROOT, "playbooks", "config_regenerate.yml"))
