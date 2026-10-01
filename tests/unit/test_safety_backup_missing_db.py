"""Guard: the pre-restore safety backup tolerates a missing wiki database.

The safety backup used to dump every wiki in wikis.yaml, so a single missing
database (the very disaster a restore recovers from) failed mariadb-dump and
aborted the whole restore. The dump must run only over wikis whose database is
present, and warn about the rest. A database that is present but cannot be
dumped is left out of the safety snapshot, which is then tagged INCOMPLETE.
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
    dump = _by_name(
        "Dump each present wiki's database group for safety backup")
    assert dump is not None, "safety dump task missing/renamed"
    assert dump.get("vars", {}).get("_backup_wikis") == {
        "db_groups": "{{ _safety_present_groups | default([]) }}"}, (
        "safety dump must cover groups built from wikis with a present DB, "
        "not all wiki_ids (a missing DB otherwise aborts the whole restore)")


def test_safety_dump_survives_a_database_that_cannot_be_dumped():
    dump = _by_name(
        "Dump each present wiki's database group for safety backup")
    assert dump.get("ansible.builtin.include_tasks") == \
        "backup_stage_db_dumps.yml", (
        "safety dump must use the backup dump, which retries a failed group "
        "one database at a time and records failures instead of failing — "
        "a broken view otherwise aborts the restore that would repair it")


def test_safety_snapshot_is_tagged_incomplete_when_a_dump_failed():
    tag = _by_name("Tag the safety snapshot")
    expr = tag["ansible.builtin.set_fact"]["_safety_tag"]
    assert "'safety-before-restore'" in expr
    assert "'INCOMPLETE'" in expr and "'missing-db:'" in expr
    assert "_backup_dump_failures | length > 0" in expr
    run = _by_name("Run safety backup with staged files")
    args = run["vars"]["backup_args"]
    assert args[args.index("--tag") + 1] == "{{ _safety_tag }}"


def test_safety_groups_drop_databases_that_do_not_exist():
    build = _by_name("Build safety dump groups from the databases that exist")
    assert build is not None, "safety group build task missing/renamed"
    facts = build["ansible.builtin.set_fact"]
    assert "select('in', _safety_existing_dbs)" in facts[
        "_safety_present_groups"], (
        "each group must be filtered to databases that exist — a declared "
        "extra database that was dropped otherwise aborts the safety backup")
    assert build.get("when") == "item.wiki in _safety_present_wikis"


def test_missing_extra_databases_are_warned_about():
    warn = _by_name("Warn about extra databases skipped from the safety backup")
    assert warn is not None and "_safety_missing_extras" in str(warn.get("when"))


def test_present_missing_partition_is_computed():
    part = _by_name("Partition wikis into present and missing databases")
    assert part is not None
    facts = part["ansible.builtin.set_fact"]
    assert "intersect" in facts["_safety_present_wikis"]
    assert "difference" in facts["_safety_missing_wikis"]


def test_missing_wikis_are_warned_about():
    warn = _by_name("Warn about wikis skipped from the safety backup")
    assert warn is not None and "_safety_missing_wikis" in str(warn.get("when"))
