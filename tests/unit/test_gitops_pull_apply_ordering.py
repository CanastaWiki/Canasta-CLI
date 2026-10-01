"""Order of the apply steps in a Compose `canasta gitops pull`.

Two signals must survive a pull that stops partway:

- Rendering rewrites .env and config/wikis.yaml before steps that can still
  refuse (my.cnf, media hosts, profiles). If the restart decision compared
  against the files as this run found them, a retry would start from the
  already-rendered files and report no restart for a change that needs one.
  The pre-render state is kept on disk in .gitops-pull-baseline until the
  pull completes, and a retry reuses it.

- .gitops-applied decides "Already up to date". Written before the backup
  schedule is re-applied, a failure there would leave the crontab stale with
  no pull ever re-applying it. It is written only after every apply step.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")
GITIGNORE = os.path.join(REPO_ROOT, "roles", "gitops", "files", "gitignore.default")
BASELINE = ".gitops-pull-baseline"


def _tasks():
    with open(PULL) as f:
        return yaml.safe_load(f)


def _index(tasks, pred, what):
    for i, t in enumerate(tasks):
        if pred(t):
            return i
    pytest.fail("no task found: %s" % what)


def _named(name):
    return lambda t: t.get("name") == name


def _includes(fragment):
    return lambda t: fragment in str(t.get("ansible.builtin.include_tasks", ""))


def _writes(path):
    def pred(t):
        c = t.get("ansible.builtin.copy") or {}
        return str(c.get("dest", "")).endswith("/" + path)
    return pred


def _removes(path):
    def pred(t):
        f = t.get("ansible.builtin.file") or {}
        return (str(f.get("path", "")).endswith("/" + path)
                and f.get("state") == "absent")
    return pred


def _env():
    jinja2 = pytest.importorskip("jinja2")
    from ansible.plugins.filter.core import FilterModule
    from ansible.plugins.test.core import TestModule

    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.tests.update(TestModule().tests())
    return env


def _stat(*checksums):
    return {"results": [
        {"stat": {"exists": True, "checksum": c}} for c in checksums
    ]}


class TestAppliedMarkerIsWrittenLast:
    def test_applied_marker_follows_every_apply_step(self):
        tasks = _tasks()
        applied = _index(tasks, _writes(".gitops-applied"), ".gitops-applied write")
        for fragment in ("_render_env.yml", "sync_compose_profiles.yml",
                         "_render_mycnf.yml", "_render_media_hosts.yml",
                         "backup_schedule_rematerialize.yml"):
            step = _index(tasks, _includes(fragment), fragment)
            assert step < applied, (
                "%s must run before .gitops-applied is written; a failure "
                "there would otherwise read as 'Already up to date'" % fragment
            )

    def test_applied_marker_follows_admin_password_files(self):
        tasks = _tasks()
        applied = _index(tasks, _writes(".gitops-applied"), ".gitops-applied write")
        passwords = _index(tasks, _named("Write admin password files from vars"),
                           "admin password write")
        assert passwords < applied


class TestRestartSignalSurvivesAPartialPull:
    def test_baseline_is_recorded_before_anything_is_rendered(self):
        tasks = _tasks()
        render = _index(tasks, _includes("_render_env.yml"), "_render_env.yml")
        read = _index(tasks, _named("Read the baseline left by an unfinished pull"),
                      "baseline read")
        write = _index(tasks, _writes(BASELINE), "baseline write")
        assert read < write < render

    def test_baseline_is_removed_only_after_the_pull_is_applied(self):
        tasks = _tasks()
        applied = _index(tasks, _writes(".gitops-applied"), ".gitops-applied write")
        remove = _index(tasks, _removes(BASELINE), "baseline removal")
        assert applied < remove

    def test_baseline_is_not_cleared_on_the_already_up_to_date_path(self):
        """The no-op exit happens before the baseline is ever written."""
        tasks = _tasks()
        skip = _index(
            tasks,
            lambda t: any(s.get("ansible.builtin.meta") == "end_play"
                          for s in t.get("block", []) if isinstance(s, dict)),
            "Already up to date exit",
        )
        assert skip < _index(tasks, _writes(BASELINE), "baseline write")

    def test_baseline_file_is_ignored_by_git(self):
        with open(GITIGNORE) as f:
            rules = [line.strip() for line in f]
        assert BASELINE in rules

    def _baseline(self, raw, pre):
        task = next(t for t in _tasks()
                    if t.get("name") == "Set the pre-render baseline")
        out = _env().from_string(
            task["ansible.builtin.set_fact"]["_pull_baseline"]
        ).render(_pull_baseline_raw=raw, _pull_pre_render_stat=pre).strip()
        return yaml.safe_load(out)

    def test_first_attempt_records_the_current_files(self):
        assert self._baseline({}, _stat("old-env", "old-wikis")) == {
            "env": "old-env", "wikis": "old-wikis",
        }

    def test_retry_reuses_the_recorded_baseline(self):
        import base64
        import json

        raw = {"content": base64.b64encode(
            json.dumps({"env": "old-env", "wikis": "old-wikis"}).encode()
        ).decode()}
        assert self._baseline(raw, _stat("new-env", "new-wikis")) == {
            "env": "old-env", "wikis": "old-wikis",
        }

    def _needs_restart(self, baseline, post, changed=()):
        task = next(t for t in _tasks()
                    if t.get("name") == "Determine if restart is needed")
        out = _env().from_string(
            task["ansible.builtin.set_fact"]["_pull_needs_restart"]
        ).render(
            _pull_baseline=baseline,
            _pull_post_render_stat=post,
            _pull_changed_files={"stdout_lines": list(changed)},
        ).strip()
        return out == "True"

    def test_restart_is_judged_against_the_baseline(self):
        baseline = {"env": "old-env", "wikis": "w"}
        assert self._needs_restart(baseline, _stat("new-env", "w"))
        assert not self._needs_restart(baseline, _stat("old-env", "w"))

    def test_wikis_change_needs_restart(self):
        baseline = {"env": "e", "wikis": "old"}
        assert self._needs_restart(baseline, _stat("e", "new"))

    def test_first_render_needs_restart(self):
        assert self._needs_restart({"env": "", "wikis": ""}, _stat("e", ""))

    def test_restart_decision_does_not_read_this_runs_starting_files(self):
        src = open(PULL).read()
        assert "_pull_old_env" not in src and "_pull_old_wikis" not in src
