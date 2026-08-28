"""What `backup schedule list` actually prints for a retention policy.

The schedule reports retention in restic's own flags — the same words
that would set it again — because a policy of several `--keep-*` flags
has no single duration to name.

The rendered line had structural assertions only, so widening the
scheduler to the full policy changed it with no unit test noticing; it
surfaced as a remote integration failure on main, after the merge. These
tests render the task file's own Jinja against crontab lines and assert
on the resulting text.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
LIST = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_list.yml")

CRON = "0 4 * * *"
INSTANCE = "demo"


def _by_name(name):
    with open(LIST) as f:
        tasks = yaml.safe_load(f)

    def walk(items):
        for t in items:
            if not isinstance(t, dict):
                continue
            if t.get("name") == name:
                return t
            for key in ("block", "always", "rescue"):
                if key in t:
                    found = walk(t[key])
                    if found:
                        return found
        return None

    task = walk(tasks)
    assert task is not None, "no task named %r in %s" % (name, LIST)
    return task


def _env():
    jinja2 = pytest.importorskip("jinja2")
    from ansible.plugins.filter.core import FilterModule
    from ansible.plugins.test.core import TestModule

    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.tests.update(TestModule().tests())
    return env


def _render(expr, **ctx):
    return _env().from_string(expr).render(**ctx)


def _auto_purge_line(job_line):
    """The rendered 'Auto-purge:' text for a crontab job line."""
    cmd = job_line.split(None, 5)[5]

    purge_expr = _by_name(
        "Read the scheduled retention policy")["ansible.builtin.set_fact"][
            "_sched_purge"]
    purge = _render(purge_expr, _sched_cmd=cmd).strip()

    msg = _by_name("Display schedule")["ansible.builtin.debug"]["msg"]
    out = _render(msg, instance_id=INSTANCE, _sched_cron=CRON,
                  _sched_purge=purge)
    for line in out.splitlines():
        if line.startswith("Auto-purge:"):
            return line
    raise AssertionError("no Auto-purge line in:\n%s" % out)


def _job(purge_args):
    base = "%s { /usr/local/bin/canasta backup create -i %s --tag scheduled" % (
        CRON, INSTANCE)
    if not purge_args:
        return base + ' ; } >> "/i/backup.log" 2>&1'
    return "%s && /usr/local/bin/canasta backup purge -i %s %s ; } >> x 2>&1" % (
        base, INSTANCE, purge_args)


class TestAutoPurgeLine:
    def test_a_single_duration_is_shown_as_its_flag(self):
        """What --purge-older-than writes, reported as restic spells it."""
        assert _auto_purge_line(_job("--keep-within 30d")) == (
            "Auto-purge: --keep-within 30d")

    def test_a_multi_flag_policy_is_shown_as_its_flags(self):
        assert _auto_purge_line(_job("--keep-daily 7 --keep-weekly 4")) == (
            "Auto-purge: --keep-daily 7 --keep-weekly 4")

    def test_a_single_count_policy_is_shown_as_its_flag(self):
        assert _auto_purge_line(_job("--keep-last 5")) == (
            "Auto-purge: --keep-last 5")

    def test_an_age_and_a_count_together_both_appear(self):
        assert _auto_purge_line(_job("--keep-within 30d --keep-daily 7")) == (
            "Auto-purge: --keep-within 30d --keep-daily 7")

    def test_no_policy_reports_that_snapshots_accumulate(self):
        line = _auto_purge_line(_job(""))
        assert line.startswith("Auto-purge: not configured")
        assert "accumulate" in line
