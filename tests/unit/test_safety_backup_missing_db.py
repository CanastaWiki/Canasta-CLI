"""Guard: the pre-restore safety backup tolerates a missing wiki database.

The safety backup used to dump every wiki in wikis.yaml, so a single missing
database (the very disaster a restore recovers from) failed mariadb-dump and
aborted the whole restore. The dump must run only over wikis whose database is
present, and warn about the rest.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


def _flatten(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            if key in t:
                yield from _flatten(t[key])


def _tasks():
    with open(COMPOSE) as f:
        return list(_flatten(yaml.safe_load(f)))


def _by_name(name):
    return next((t for t in _tasks() if t.get("name") == name), None)


def test_safety_dump_loops_only_over_present_wikis():
    dump = _by_name("Dump each present wiki database for safety backup")
    assert dump is not None, "safety dump task missing/renamed"
    assert dump.get("loop") == "{{ _safety_present_wikis }}", (
        "safety dump must loop over wikis with a present DB, not all wiki_ids "
        "(a missing DB otherwise aborts the whole restore)")


def test_present_missing_partition_is_computed():
    part = _by_name("Partition wikis into present and missing databases")
    assert part is not None
    facts = part["ansible.builtin.set_fact"]
    assert "intersect" in facts["_safety_present_wikis"]
    assert "difference" in facts["_safety_missing_wikis"]


def test_missing_wikis_are_warned_about():
    warn = _by_name("Warn about wikis skipped from the safety backup")
    assert warn is not None and "_safety_missing_wikis" in str(warn.get("when"))
