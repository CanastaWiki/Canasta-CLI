"""upgrade fails for an instance that came up but does not serve its wikis.

A failed startup composer update and wikis that do not answer through the
edge used to be printed only, so the upgrade reported success and exited 0
with every wiki down. Both now reach _upgrade_failures, which fails the
playbook once the loop is done.

Runs the real recording task from _upgrade_single.yml.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SINGLE = os.path.join(REPO_ROOT, "playbooks", "_upgrade_single.yml")
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
REPORT = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "report_composer_failure.yml")
RECORD = "Record an upgraded instance that is not serving"

UNANSWERED = [{"wiki": "main", "url": "http://x/", "reason": "HTTP 503"},
              {"wiki": "docs", "url": "http://x/docs", "reason": "HTTP 503"}]


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _walk(items):
    for item in items:
        yield item
        for key in ("block", "rescue", "always"):
            yield from _walk(item.get(key) or [])


def _named(path, name):
    return next(t for t in _walk(_load(path)) if t.get("name") == name)


def _failures(tmp_path, **facts):
    variables = {"instance_id": "site", "_upgrade_skip": False,
                 "_was_running": True, "_upgrade_restart_failed": False,
                 "_composer_update_failure": "",
                 "_edge_probe": {"unanswered": []},
                 "ansible_python_interpreter": sys.executable}
    variables.update(facts)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": variables,
        "tasks": [_named(SINGLE, RECORD), {"ansible.builtin.debug": {
            "msg": "FAILURES=<<{{ _upgrade_failures | default([]) | join('|') }}>>"}}],
    }]))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,", str(play)], capture_output=True,
        text=True, cwd=tmp_path,
        env=dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.split("FAILURES=<<", 1)[1].split(">>", 1)[0]


class TestTheFailureIsRecorded:
    def test_wikis_that_do_not_answer(self, tmp_path):
        assert _failures(tmp_path, _edge_probe={"unanswered": UNANSWERED}) == (
            "'site': upgraded, but wikis main, docs do not answer through "
            "the HAProxy edge")

    def test_a_failed_composer_update(self, tmp_path):
        assert _failures(
            tmp_path, _composer_update_failure="Composer update failed (exit 2).",
        ) == ("'site': upgraded, but the startup composer update failed: "
              "Composer update failed (exit 2).")

    def test_both_are_one_failure(self, tmp_path):
        failures = _failures(
            tmp_path, _composer_update_failure="Composer update failed (exit 2).",
            _edge_probe={"unanswered": UNANSWERED})
        assert "|" not in failures
        assert "composer update failed" in failures
        assert "do not answer" in failures

    def test_a_serving_instance_records_nothing(self, tmp_path):
        assert _failures(tmp_path) == ""

    def test_a_skipped_instance_ignores_the_previous_instance(self, tmp_path):
        assert _failures(
            tmp_path, _upgrade_skip=True,
            _composer_update_failure="Composer update failed (exit 2).",
            _edge_probe={"unanswered": UNANSWERED}) == ""

    def test_a_stopped_instance_ignores_a_stale_probe(self, tmp_path):
        assert _failures(tmp_path, _was_running=False,
                         _edge_probe={"unanswered": UNANSWERED}) == ""


class TestTheSignalsAreFresh:
    def test_the_composer_failure_is_reset_for_each_instance(self):
        reset = _named(MAIN, "Reset the restart failure signals for this instance")
        assert reset["ansible.builtin.set_fact"]["_composer_update_failure"] == ""

    def test_the_composer_failure_is_set_unconditionally(self):
        note = _named(REPORT, "Note whether the startup composer update failed")
        assert "when" not in note

    def test_recorded_after_the_probe(self):
        names = [t.get("name") for t in _load(SINGLE)]
        assert names.index("Report wikis that do not answer through the edge") < (
            names.index(RECORD))
