"""Guard: canasta backup purge supports --dry-run.

Purging deletes snapshots irreversibly, so the retention policy has to be
previewable before it runs for real. The flag is a passthrough to restic's
own `forget --dry-run`, which reports what would go and leaves the
repository untouched.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PURGE_TASKS = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "purge.yml")
DEFINITIONS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _iter_tasks(tasks):
    for t in tasks:
        yield t
        if isinstance(t, dict) and "block" in t:
            yield from _iter_tasks(t["block"])


def _purge_command():
    for c in _load(DEFINITIONS)["commands"]:
        if c["name"] == "backup_purge":
            return c
    raise AssertionError("backup_purge is not defined")


class TestDefinition:
    def test_dry_run_parameter_exists(self):
        params = {p["name"]: p for p in _purge_command()["parameters"]}
        assert "dry_run" in params, "backup purge must expose a dry-run flag"

    def test_dry_run_is_an_off_by_default_bool(self):
        p = {p["name"]: p for p in _purge_command()["parameters"]}["dry_run"]
        assert p["type"] == "bool"
        # A dry run that defaulted on would make the command a no-op; a
        # string-typed flag would make "--dry-run" swallow the next token.
        assert p.get("default") is False

    def test_dry_run_renders_as_hyphenated_flag(self):
        p = {p["name"]: p for p in _purge_command()["parameters"]}["dry_run"]
        assert p.get("long") == "dry-run"

    def test_dry_run_is_documented_in_an_example(self):
        assert any("--dry-run" in e for e in _purge_command()["examples"])


class TestTasks:
    def test_dry_run_flag_is_appended_when_requested(self):
        task = next(
            (t for t in _iter_tasks(_load(PURGE_TASKS))
             if "--dry-run" in str(t.get("ansible.builtin.set_fact", ""))),
            None,
        )
        assert task is not None, "no task appends --dry-run to the restic args"
        assert "dry_run" in str(task.get("when", "")), (
            "the --dry-run flag must be gated on the dry_run parameter"
        )

    def test_dry_run_defaults_off_in_the_task_guard(self):
        """An undefined dry_run must not fail the play or enable the flag."""
        task = next(
            t for t in _iter_tasks(_load(PURGE_TASKS))
            if "--dry-run" in str(t.get("ansible.builtin.set_fact", ""))
        )
        when = str(task["when"])
        assert "default(false)" in when and "bool" in when

    def test_the_run_is_still_a_forget_prune(self):
        """--dry-run must ride along with the real command, not replace it."""
        base = next(
            t for t in _iter_tasks(_load(PURGE_TASKS))
            if t.get("name") == "Build purge arguments"
        )
        args = base["ansible.builtin.set_fact"]["_purge_list"]
        assert args[:2] == ["forget", "--prune"]

    def test_dry_run_result_is_labeled_as_a_preview(self):
        """Restic's output alone doesn't say the repo was left alone."""
        note = next(
            (t for t in _iter_tasks(_load(PURGE_TASKS))
             if "dry_run" in str(t.get("when", ""))
             and "ansible.builtin.debug" in t),
            None,
        )
        assert note is not None, (
            "a dry run must say so, or its output reads like a real purge"
        )
