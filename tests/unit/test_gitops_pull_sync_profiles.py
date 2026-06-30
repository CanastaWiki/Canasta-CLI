"""Structural guard for COMPOSE_PROFILES reconciliation on `gitops pull`.

A Compose `gitops pull` renders `.env` verbatim from env.template + host
vars, so without reconciliation a stale COMPOSE_PROFILES literal — or a
render that dropped internal-db / a feature profile — survives the pull and
leaves services running-but-unmanaged. The pull must run the same
sync_compose_profiles used before start, and it must run BEFORE change
detection so a profile change correctly flags a restart.

This path mutates a real .env / containers, so it is skipped by the CI
integration suite; this structural assertion is the CI-runnable guard.
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml"
)


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _includes(task):
    inc = task.get("ansible.builtin.include_tasks") or task.get("include_tasks")
    if isinstance(inc, dict):
        return inc.get("file", "")
    return inc or ""


def test_pull_compose_reconciles_profiles_after_render():
    tasks = _tasks(PULL_COMPOSE)
    sync_idx = next(
        (i for i, t in enumerate(tasks)
         if "sync_compose_profiles.yml" in _includes(t)),
        None,
    )
    assert sync_idx is not None, (
        "pull_compose.yml must reconcile COMPOSE_PROFILES "
        "(include sync_compose_profiles.yml) after rendering .env"
    )

    # Must run before change detection so a reconciled profile change is
    # picked up by the new-.env comparison and flags a restart.
    change_idx = next(
        (i for i, t in enumerate(tasks)
         if t.get("name") == "Read new .env for comparison"),
        None,
    )
    assert change_idx is not None, "expected the new-.env comparison step"
    assert sync_idx < change_idx, (
        "profile reconciliation must run before change detection so a "
        "profile change is seen by the .env comparison"
    )
