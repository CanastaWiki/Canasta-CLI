"""Bouncer enrollment's own config set doesn't repeat config set's report.

Enabling CrowdSec restarts the instance, then enrollment stores the bouncer
key with a nested config set, which restarts again. Enrollment announces that
restart and its result itself, so the nested call must not also print the
generic "Restarting instance…" and "Settings updated." lines.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SET = os.path.join(REPO_ROOT, "roles", "config", "tasks", "set.yml")
ENROLL = os.path.join(REPO_ROOT, "roles", "crowdsec", "tasks",
                      "bouncer_enroll.yml")


def _tasks(path):
    with open(path) as f:
        return {t.get("name"): t for t in yaml.safe_load(f) or []
                if isinstance(t, dict)}


def _when(task):
    w = task.get("when", [])
    return w if isinstance(w, list) else [w]


def test_config_set_reports_are_skippable_by_the_caller():
    tasks = _tasks(SET)
    for name in ("Report restarting to apply changes",
                 "Report settings applied"):
        assert any("config_set_caller_reports" in str(c)
                   for c in _when(tasks[name])), name


def test_the_restart_itself_is_not_skipped():
    restart = _tasks(SET)["Restart instance to apply changes"]
    assert not any("config_set_caller_reports" in str(c)
                   for c in _when(restart))


def test_enrollment_passes_the_flag():
    store = _tasks(ENROLL)["Store the key, re-render the Caddyfile, and restart"]
    assert store["vars"].get("config_set_caller_reports") is True
