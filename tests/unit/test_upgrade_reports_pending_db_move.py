"""upgrade says when a stopped instance's database account move waits.

The move off the database root account runs only on an instance that is
running during the upgrade. `--dry-run` reports, per instance, whether the
move would run or would wait because the instance is stopped, and the end
of the upgrade names each instance left stopped with the move pending. Both
decide from the instance's current settings through the same check the
move itself uses.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PLAYBOOKS = os.path.join(REPO_ROOT, "playbooks")
UPGRADE = os.path.join(PLAYBOOKS, "upgrade.yml")
DRY_RUN = os.path.join(PLAYBOOKS, "_upgrade_dry_run_instance.yml")
MIGRATE = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks",
                       "migrate_wiki_db_account.yml")
CHECK_PATH = "roles/upgrade/tasks/check_wiki_db_account.yml"


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _named(tasks, name):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        if task.get("name") == name:
            return task
        for key in ("block", "rescue", "always"):
            found = _named(task.get(key), name)
            if found:
                return found
    return None


def _render(expr, **variables):
    return Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(expr))


def _truthy(template, **variables):
    return _render("{{ " + template + " }}", **variables) in (True, "True")


class TestSharedCheck:
    def test_the_move_and_the_dry_run_use_one_check(self):
        migrate = _named(_load(MIGRATE),
                         "Check whether MediaWiki waits to move to its own database account")
        assert migrate["ansible.builtin.include_tasks"] == "check_wiki_db_account.yml"
        dry = _named(_load(DRY_RUN),
                     "Check whether MediaWiki waits to move to its own database account")
        assert dry["ansible.builtin.include_tasks"].endswith(CHECK_PATH)

    def test_the_dry_run_checks_running_like_the_upgrade(self):
        task = _named(_load(DRY_RUN), "Check whether the instance is running")
        assert task["ansible.builtin.include_tasks"].endswith(
            "roles/orchestrator/tasks/check_running.yml")


class TestDryRun:
    def test_every_upgraded_instance_is_checked(self):
        task = _named(_load(UPGRADE), "Report each instance's database account move")
        assert task["ansible.builtin.include_tasks"] == "_upgrade_dry_run_instance.yml"
        assert "_edge_refused" in task["loop"]

    @pytest.mark.parametrize("running, phrase", [
        (True, "would move MediaWiki of 'mysite' off the database root account"),
        (False, "would leave 'mysite' stopped and skip moving MediaWiki"),
    ])
    def test_the_report_says_whether_the_move_runs(self, running, phrase):
        task = _named(_load(DRY_RUN), "Report the database account move")
        msg = _render(task["ansible.builtin.debug"]["msg"],
                      _container_running=running, instance_id="mysite")
        assert phrase in msg

    @pytest.mark.parametrize("exists, pending, shown", [
        (True, True, True),
        (True, False, False),
        (False, True, False),
    ])
    def test_only_a_pending_move_is_reported(self, exists, pending, shown):
        task = _named(_load(DRY_RUN), "Report the database account move")
        variables = {"_upgrade_dry_env": {"stat": {"exists": exists}},
                     "_mig_db_pending": pending}
        assert all(_truthy(c, **variables) for c in task["when"]) is shown


class TestSummary:
    @pytest.mark.parametrize("running, pending, recorded", [
        (False, True, True),
        (True, True, False),
        (False, False, False),
    ])
    def test_a_stopped_instance_with_the_move_pending_is_recorded(
            self, running, pending, recorded):
        task = _named(_load(MIGRATE), "Record the move left for a stopped instance")
        variables = {"_was_running": running, "_mig_db_pending": pending}
        assert all(_truthy(c, **variables) for c in task["when"]) is recorded
        assert _render(task["ansible.builtin.set_fact"]["_upgrade_db_move_pending"],
                       _upgrade_db_move_pending=["a"], instance_id="b") == ["a", "b"]

    def test_the_end_of_the_upgrade_names_them(self):
        tasks = _load(UPGRADE)
        task = _named(tasks, "Report the database account moves left for stopped instances")
        msg = _render(task["ansible.builtin.debug"]["msg"],
                      _upgrade_db_move_pending=["one", "two"])
        assert "Left stopped with the database account move pending: one, two." in msg
        assert "'canasta upgrade' again after 'canasta start'" in msg
        assert _truthy(task["when"], _upgrade_db_move_pending=["one"])
        assert not _truthy(task["when"])
