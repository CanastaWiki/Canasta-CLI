"""A start that leaves a service uncreated has to fail.

`up -d` exiting 0 is not evidence that every service started: podman-compose
reported success while `crowdsec` — whose image reference it could not
resolve — was simply absent. The post-start checks covered `web` alone, so
`canasta restart` printed four `Error:` lines and still exited 0, leaving an
instance running without the service it had just been told to enable.

Absence is the test, not "not running": `observable-init` is a one-shot
(`restart: "no"`) whose container exits by design.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
START = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")

PRESENT = "List the services that have a container"
DETERMINE = "Determine which expected services never started"
FAIL = "Fail when an expected service never started"


def _compose_block():
    with open(START) as f:
        tasks = yaml.safe_load(f)
    for t in tasks:
        if isinstance(t, dict) and t.get("name") == "Start (Docker Compose)":
            return t["block"]
    raise AssertionError("the Compose start block is gone or renamed")


def _names():
    return [t.get("name") for t in _compose_block() if isinstance(t, dict)]


def _by_name(name):
    for t in _compose_block():
        if isinstance(t, dict) and t.get("name") == name:
            return t
    raise AssertionError("no task named %r in the Compose start block" % name)


def _render(expr, **ctx):
    jinja2 = pytest.importorskip("jinja2")
    from ansible.plugins.filter.core import FilterModule
    # `difference` is a set filter, which lives apart from the core ones.
    from ansible.plugins.filter.mathstuff import FilterModule as MathFilters
    from ansible.plugins.test.core import TestModule

    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.filters.update(MathFilters().filters())
    env.tests.update(TestModule().tests())
    return env.from_string(expr).render(**ctx)


def _absent(expected_rc, expected, present):
    expr = _by_name(DETERMINE)["ansible.builtin.set_fact"][
        "_start_absent_services"]
    import ast
    return ast.literal_eval(_render(
        expr,
        _expected_services={"rc": expected_rc, "stdout": "\n".join(expected)},
        _start_present_services={"stdout": "\n".join(present)},
    ).strip())


class TestTheCheckExists:
    def test_all_three_tasks_are_present(self):
        names = _names()
        for name in (PRESENT, DETERMINE, FAIL):
            assert name in names, "missing task: %s" % name

    def test_it_lists_every_container_not_only_running_ones(self):
        """A one-shot's container has exited; absence is what matters."""
        argv = _by_name(PRESENT)["ansible.builtin.command"]["argv"]
        assert "-a" in argv, (
            "without -a an exited one-shot looks absent and every start "
            "with the observable profile would fail"
        )
        assert "status=running" not in " ".join(str(a) for a in argv)

    def test_it_runs_after_the_start_and_before_the_web_checks(self):
        names = _names()
        assert names.index("Start containers") < names.index(PRESENT)
        assert names.index(FAIL) < names.index("Get web container id"), (
            "the specific failure should be reported before the generic "
            "web-container one"
        )

    def test_the_failure_is_gated_on_something_being_absent(self):
        when = str(_by_name(FAIL).get("when", ""))
        assert "_start_absent_services" in when and "length > 0" in when


class TestWhatCountsAsAbsent:
    def test_a_service_with_no_container_is_absent(self):
        assert _absent(0, ["db", "web", "crowdsec"], ["db", "web"]) == [
            "crowdsec"]

    def test_a_fully_started_stack_reports_nothing(self):
        assert _absent(0, ["db", "web"], ["db", "web"]) == []

    def test_a_one_shot_that_exited_is_not_absent(self):
        """observable-init has a container; it just is not running."""
        assert _absent(
            0, ["db", "web", "observable-init"],
            ["db", "web", "observable-init"]) == []

    def test_an_unreadable_expected_list_is_not_a_failure(self):
        """An unknown answer must not be reported as a missing service."""
        assert _absent(1, [], ["db", "web"]) == []

    def test_extra_running_services_are_not_reported(self):
        """A sidecar outside the active profiles is not a problem."""
        assert _absent(0, ["db"], ["db", "some-sidecar"]) == []


class TestTheMessage:
    def _msg(self, absent):
        return " ".join(_render(
            _by_name(FAIL)["ansible.builtin.fail"]["msg"],
            _start_absent_services=absent, instance_id="demo",
        ).split())

    def test_it_names_the_service_and_the_instance(self):
        out = self._msg(["crowdsec"])
        assert "crowdsec" in out and "demo" in out

    def test_it_agrees_in_number(self):
        assert "which has no container" in self._msg(["crowdsec"])
        assert "which have no container" in self._msg(["crowdsec", "varnish"])

    def test_it_says_the_compose_command_reported_success(self):
        """Otherwise the operator looks for a compose error that is not there."""
        assert "reported success" in self._msg(["crowdsec"])
