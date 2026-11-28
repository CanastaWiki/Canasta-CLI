"""`canasta upgrade` backfills wikis.yaml.template on every instance.

gitops tracks wikis.yaml.template, so a repo that predates it gains the
template on upgrade; the backfill itself no-ops on a non-gitops instance.
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
GITOPS_VARS = os.path.join(REPO_ROOT, "roles", "gitops", "vars", "main.yml")


def _backfill_task():
    with open(MAIN) as f:
        tasks = yaml.safe_load(f)
    for t in tasks:
        if str(t.get("ansible.builtin.include_tasks", "")).strip().endswith(
                "/backfill_wikis_template.yml"):
            return t
    raise AssertionError("upgrade no longer includes backfill_wikis_template.yml")


def test_the_include_is_not_wrapped_in_a_block():
    assert "block" not in _backfill_task()


def test_runs_unconditionally():
    assert "when" not in _backfill_task()


def test_gitops_tracks_the_template():
    with open(GITOPS_VARS) as f:
        gitops_vars = yaml.safe_load(f)
    assert "wikis.yaml.template" in gitops_vars["gitops_tracked_paths"]
