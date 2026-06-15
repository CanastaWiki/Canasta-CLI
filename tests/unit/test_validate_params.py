"""Tests for the required-parameter check in validate_params.yml.

A required value that arrives present-but-blank (e.g. --repo="" from a
failed `$(...)` substitution) must be reported as "given but is empty"
rather than "missing", so the empty-substitution case is self-explanatory
(#669). These tests render the task's real `fail.msg` Jinja against a
stubbed `lookup('vars', ...)` so a regression in the wording or in the
empty-vs-missing distinction is caught.
"""

import os

import jinja2
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
VALIDATE_PARAMS = os.path.join(
    REPO_ROOT, "roles", "common", "tasks", "validate_params.yml",
)

# Sentinel matching the one used by an absent argument: a missing var is
# never written by canasta.py, so the YAML uses this default to tell an
# absent argument apart from one that is present but empty.
UNDEFINED = "__absent__"


def _load_tasks():
    with open(VALIDATE_PARAMS) as f:
        return yaml.safe_load(f)


def _required_check_msg():
    for task in _load_tasks():
        if task.get("name") == "Check required parameters":
            return task["ansible.builtin.fail"]["msg"]
    raise AssertionError("'Check required parameters' task not found")


def _required_check_when():
    for task in _load_tasks():
        if task.get("name") == "Check required parameters":
            return task["when"]
    raise AssertionError("'Check required parameters' task not found")


def _render_msg(item, present_vars):
    """Render the fail.msg as Ansible would, with lookup('vars', ...) stubbed.

    `present_vars` maps variable name -> value for vars that reached
    Ansible; names absent from it model an argument never supplied.
    """

    def lookup(plugin, name, default="__canasta_undef__"):
        assert plugin == "vars"
        return present_vars.get(name, default)

    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.globals["lookup"] = lookup
    return env.from_string(_required_check_msg()).render(item=item).strip()


REPO = {"name": "repo"}
POSITIONAL = {"name": "wiki", "positional": True}


def test_absent_required_flag_reports_missing():
    msg = _render_msg(REPO, present_vars={})
    assert msg == "Required parameter '--repo' is missing"


def test_empty_required_flag_reports_given_but_empty():
    msg = _render_msg(REPO, present_vars={"repo": ""})
    assert msg == "Parameter '--repo' was given but is empty (got '')"


def test_whitespace_required_flag_reports_given_but_empty():
    msg = _render_msg(REPO, present_vars={"repo": "   "})
    assert msg == "Parameter '--repo' was given but is empty (got '   ')"


def test_absent_positional_arg_reports_missing():
    msg = _render_msg(POSITIONAL, present_vars={})
    assert msg == "Required argument WIKI is missing"


def test_empty_positional_arg_reports_given_but_empty():
    msg = _render_msg(POSITIONAL, present_vars={"wiki": ""})
    assert msg == "Argument WIKI was given but is empty (got '')"


def test_when_trims_so_whitespace_only_values_are_rejected():
    # The blank test must trim, otherwise a whitespace-only required value
    # slips past validation and fails later with an opaque error.
    when = " ".join(str(c) for c in _required_check_when())
    assert "| trim) == ''" in when
