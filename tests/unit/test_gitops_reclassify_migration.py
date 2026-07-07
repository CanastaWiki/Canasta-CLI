"""The push-time reclassification migration moves pre-classifier cleartext
secret literals out of env.template into the git-crypted vars.yaml. It must use
the canonical classifier, re-placeholder the template, and never leak a value."""
import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
MIG = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                   "_migrate_reclassified_secrets.yml")
PUSH = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml")


def _read(p):
    with open(p) as f:
        return f.read()


def _walk(tasks):
    for t in tasks:
        yield t
        if isinstance(t, dict) and "block" in t:
            yield from _walk(t["block"])


def test_push_runs_reclassification_migration():
    assert "_migrate_reclassified_secrets.yml" in _read(PUSH)


def test_migration_uses_canonical_classifier_and_replaceholders():
    c = _read(MIG)
    assert "canasta_secret_key_regex" in c, "must classify via the canonical regex"
    assert "lineinfile" in c, "must re-placeholder the migrated key in env.template"


def test_migration_report_lists_names_not_values():
    for t in _walk(yaml.safe_load(_read(MIG))):
        debug = t.get("ansible.builtin.debug") or t.get("debug")
        if debug:
            assert "keys()" in debug.get("msg", ""), (
                "migration report must list key names only, never values"
            )


def test_migration_value_bearing_tasks_are_no_log():
    flagged = 0
    for t in _walk(yaml.safe_load(_read(MIG))):
        name = (t.get("name") or "").lower()
        if any(s in name for s in ("env.template", "move map", "host vars",
                                   "literal secret")):
            flagged += 1
            assert t.get("no_log") is True, "%r must be no_log" % name
    assert flagged >= 3, "expected the value-bearing tasks to be guarded"
