"""What `canasta gitops pull` prints when it finishes.

The report was built from three ternaries that rendered '' for every
outcome that did not apply, inside a `>-` scalar whose continuation lines
were indented past the block's first line — so YAML kept the newlines
around them. A pull needing nothing printed a trailing space and a blank
line; a pull needing both printed an empty third line.

The outcomes are collected as a list and joined, so each combination is
one sentence, naming what made each outcome necessary.
"""

import ast
import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")


def _find(items, name):
    for t in items or []:
        if not isinstance(t, dict):
            continue
        if t.get("name") == name:
            return t
        for key in ("block", "always", "rescue"):
            if key in t:
                found = _find(t[key], name)
                if found:
                    return found
    return None


def _tasks():
    with open(PULL) as f:
        return yaml.safe_load(f)


def _env():
    jinja2 = pytest.importorskip("jinja2")
    from ansible.plugins.filter.core import FilterModule
    from ansible.plugins.test.core import TestModule

    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.tests.update(TestModule().tests())
    return env


RESTART = [".env", "config/Caddyfile.site"]
MAINTENANCE = ["extensions/Foo"]


def _report(restart, maintenance):
    env = _env()
    tasks = _tasks()

    summarize = _find(tasks, "Summarize what the pull requires")
    assert summarize is not None, "the summary task is gone or renamed"
    followup = ast.literal_eval(
        env.from_string(
            summarize["ansible.builtin.set_fact"]["_pull_followup"]
        ).render(
            _pull_restart_triggers=RESTART if restart is True else (restart or []),
            _pull_maintenance_triggers=(MAINTENANCE if maintenance is True
                                        else (maintenance or [])),
        ).strip()
    )

    report = _find(tasks, "Report pull results")
    assert report is not None, "the report task is gone or renamed"
    return env.from_string(report["ansible.builtin.debug"]["msg"]).render(
        _pull_prev_commit={"stdout": "aaaaaaaaaa"},
        _pull_new_commit={"stdout": "bbbbbbbbbb"},
        _pull_followup=followup,
    )


class TestPullReportIsOneSentence:
    def test_the_report_template_has_no_interior_newline(self):
        msg = _find(_tasks(), "Report pull results")["ansible.builtin.debug"]["msg"]
        assert "\n" not in msg.strip(), (
            "the report folds with a newline inside it; keep continuation "
            "lines at the block's own indentation"
        )

    def test_nothing_needed(self):
        assert _report(False, False) == (
            "Pulled aaaaaaa..bbbbbbb. No restart needed.")

    def test_restart_only(self):
        assert _report(True, False) == (
            "Pulled aaaaaaa..bbbbbbb. Restart needed (.env, "
            "config/Caddyfile.site changed).")

    def test_maintenance_only(self):
        assert _report(False, True) == (
            "Pulled aaaaaaa..bbbbbbb. Maintenance update needed "
            "(extensions/Foo changed).")

    def test_both_needed_reads_as_two_sentences(self):
        """The case the ternaries rendered with an empty line between."""
        assert _report(True, True) == (
            "Pulled aaaaaaa..bbbbbbb. Restart needed (.env, "
            "config/Caddyfile.site changed). Maintenance update needed "
            "(extensions/Foo changed).")

    def test_a_long_extension_list_is_shortened(self):
        names = ["extensions/E%d" % i for i in range(8)]
        assert _report(False, names) == (
            "Pulled aaaaaaa..bbbbbbb. Maintenance update needed "
            "(extensions/E0, extensions/E1, extensions/E2, extensions/E3, "
            "extensions/E4 and 3 more changed).")

    @pytest.mark.parametrize("restart,maintenance", [
        (False, False), (True, False), (False, True), (True, True)])
    def test_no_outcome_leaves_stray_whitespace(self, restart, maintenance):
        out = _report(restart, maintenance)
        assert out == out.strip()
        assert "  " not in out
