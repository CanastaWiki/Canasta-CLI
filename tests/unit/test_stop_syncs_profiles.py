"""Structural guard: the stop path reconciles COMPOSE_PROFILES before
`down`.

`docker compose down` only tears down services in the active profiles, so a
drifted profile set would leave a running container as an unmanaged orphan —
and, paired with a later recreate, strand Varnish on a stale backend IP. The
start path already syncs before `up`; stop must sync before `down` so a restart
recreates the whole intended set symmetrically.
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
STOP = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "stop.yml")


def _includes(task):
    inc = task.get("ansible.builtin.include_tasks") or task.get("include_tasks")
    if isinstance(inc, dict):
        return inc.get("file", "")
    return inc or ""


def _stop_block():
    with open(STOP) as f:
        tasks = yaml.safe_load(f)
    stop = next(
        (t for t in tasks if t.get("name") == "Stop (Docker Compose)"
         and t.get("block")),
        None,
    )
    assert stop is not None, "stop.yml must have a stop block"
    return stop["block"]


def test_stop_syncs_profiles_before_down():
    block = _stop_block()
    sync_idx = next(
        (i for i, t in enumerate(block)
         if "sync_compose_profiles.yml" in _includes(t)),
        None,
    )
    assert sync_idx is not None, (
        "stop.yml must reconcile COMPOSE_PROFILES (include "
        "sync_compose_profiles.yml) before `down`"
    )
    down_idx = next(
        (i for i, t in enumerate(block)
         if "down" in str(t.get("ansible.builtin.command", {}).get("cmd", ""))),
        None,
    )
    assert down_idx is not None, "stop.yml must run `docker compose ... down`"
    assert sync_idx < down_idx, "profile sync must run before `down`"
