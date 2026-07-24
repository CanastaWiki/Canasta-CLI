"""Regression guard for #1164: `canasta config regenerate` must not take down a
gitops Compose instance.

Two structural invariants:

  * config_regenerate.yml must reconcile COMPOSE_PROFILES (sync_compose_profiles)
    AFTER re-rendering the gitops templates — exactly as gitops pull does. The
    .env is re-rendered verbatim from env.template, which has no
    COMPOSE_PROFILES line, so without the reconcile the render drops the
    profiles and a later down/up won't start the database / feature containers.

  * render_compose.yml must refuse to overwrite the live config/wikis.yaml when
    a wiki url rendered empty (a missing wiki_url_<id> var) — a blank url makes
    MediaWiki's FarmConfigLoader fatal on every request.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CONFIG_REGEN = os.path.join(REPO_ROOT, "playbooks", "config_regenerate.yml")
RENDER_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "render_compose.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _include(t):
    return t.get("ansible.builtin.include_tasks") or t.get("include_tasks") or ""


class TestConfigRegenerateProfiles:
    def test_profiles_reconciled_after_render(self):
        tasks = _load(CONFIG_REGEN)
        render_i = sync_i = -1
        for i, t in enumerate(tasks):
            inc = _include(t)
            if "render_gitops_config" in inc:
                render_i = i
            if "sync_compose_profiles" in inc:
                sync_i = i
        assert render_i >= 0, (
            "config_regenerate.yml must re-render the gitops templates")
        assert sync_i > render_i, (
            "config_regenerate.yml must reconcile COMPOSE_PROFILES "
            "(sync_compose_profiles) AFTER the render, like gitops pull, or the "
            "render drops the profile set (#1164)")


class TestRenderRefusesEmptyUrl:
    def test_fail_task_guards_empty_url(self):
        guards = [
            t for t in _walk(_load(RENDER_COMPOSE))
            if ("ansible.builtin.fail" in t or "fail" in t)
            and "rejectattr('url')" in yaml.safe_dump(t)
        ]
        assert guards, (
            "render_compose.yml must fail (before writing config/wikis.yaml) "
            "when a wiki url rendered empty — rejectattr('url') on the rendered "
            "wikis list (#1164)")

    def test_wikis_yaml_written_from_validated_fact(self):
        # The render must go through a fact it can validate, not copy the
        # template loop straight to disk (which can't be checked first).
        text = open(RENDER_COMPOSE).read()
        assert "_render_wikis_content" in text, (
            "render_compose.yml must render wikis.yaml into a fact "
            "(_render_wikis_content) so it can be validated before it replaces "
            "the live file (#1164)")
