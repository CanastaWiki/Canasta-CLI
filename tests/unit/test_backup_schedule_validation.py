"""The backup schedule is validated before it reaches the crontab.

config/backup-schedule.yml is distributed by gitops and re-applied on pull;
its cron expression and retention values were spliced into the crontab line
unchecked. Only plain cron fields and known retention flags whose values
need no shell quoting are accepted.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
APPLY = os.path.join(TASKS, "backup_schedule_apply.yml")
SET = os.path.join(TASKS, "backup_schedule_set.yml")
VALIDATE = os.path.join(TASKS, "backup_schedule_validate.yml")


def _tasks(path=APPLY):
    with open(path) as f:
        return yaml.safe_load(f)


def _refused(cron, retention):
    task = next(t for t in _tasks(VALIDATE)
                if t.get("name") == "Validate the backup schedule")
    variables = {"cron_expression": cron, "retention": retention}
    variables.update({k: trust_as_template(v) if isinstance(v, str) else v
                      for k, v in task["vars"].items()})
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template("{{ %s }}" % task["when"]))


@pytest.mark.parametrize("cron, retention", [
    ("0 3 * * *", {"keep-daily": "7", "keep-within": "30d"}),
    ("*/15 2-4 1,15 JAN MON", {}),
    ("0 3 * * *", {"keep-tag": "weekly-1"}),
    ("0 3 * * *", None),
])
def test_valid_schedules_are_accepted(cron, retention):
    assert _refused(cron, retention) is False


@pytest.mark.parametrize("cron, retention", [
    ("0 3 * * *\n", {}),
    ("0 3 * * *\n* * * * * touch /tmp/x", {}),
    ("0 3 * * * curl x|sh", {}),
    ("0 3 * *", {}),
    ("0 3 * * *", {"keep-daily": "7; curl x | sh"}),
    ("0 3 * * *", {"keep-daily": "7\n"}),
    ("0 3 * * *", {"keep-daily": "$(id)"}),
    ("0 3 * * *", {"x; curl": "1"}),
    ("0 3 * * *", ["keep-daily", "7"]),
])
def test_unsafe_schedules_are_refused(cron, retention):
    assert _refused(cron, retention) is True


def test_validation_runs_before_any_change():
    names = [t.get("name") for t in _tasks()]
    assert names[0] == "Validate the backup schedule"
    assert _tasks()[0]["ansible.builtin.include_tasks"].endswith(
        "/backup_schedule_validate.yml")


def test_schedule_set_validates_before_writing_the_file():
    tasks = _tasks(SET)
    names = [t.get("name") for t in tasks]
    validate = names.index("Validate the backup schedule")
    assert validate < names.index("Persist the backup schedule to instance config")
    task = tasks[validate]
    assert task["ansible.builtin.include_tasks"].endswith(
        "/backup_schedule_validate.yml")
    assert "_retention_policy" in task["vars"]["retention"]


def _cron_retention(retention):
    task = next(t for t in _tasks() if t.get("name") == "Build backup command for cron")
    expr = task["ansible.builtin.set_fact"]["_cron_retention"]
    templar = Templar(loader=DataLoader(), variables={"retention": retention})
    return templar.template(trust_as_template(expr))


def test_retention_flags_render_as_before():
    # An empty result must be the empty string: the purge step runs only when
    # _cron_retention != '', and a purge without keep flags deletes every
    # snapshot.
    assert _cron_retention({"keep-within": "30d", "keep-daily": "7"}) == (
        "--keep-daily 7 --keep-within 30d")
    assert _cron_retention({}) == ""
    assert _cron_retention(None) == ""
