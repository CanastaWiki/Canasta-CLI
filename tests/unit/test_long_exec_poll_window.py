"""A long exec is polled for as long as it is allowed to run."""

import os

import jinja2
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
EXEC = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "exec.yml")
RESILIENT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "resilient_exec.yml")


def _long_exec_vars():
    with open(EXEC) as f:
        tasks = yaml.safe_load(f)
    task = next(t for t in tasks
                if "resilient_exec.yml" in str(t.get("ansible.builtin.include_tasks", "")))
    return task["vars"]


def _render(expr, **ctx):
    return jinja2.Environment().from_string(expr).render(**ctx)


def _poll_delay():
    with open(RESILIENT) as f:
        tasks = yaml.safe_load(f)
    poll = next(t for t in tasks if "until" in t)
    return int(_render(poll["delay"]))


class TestLongExecPollWindow:
    def test_retries_are_passed(self):
        assert "rx_retries" in _long_exec_vars()

    def test_default_window_covers_the_async_limit(self):
        v = _long_exec_vars()
        async_s = int(_render(v["rx_async"]))
        retries = int(_render(v["rx_retries"]))
        assert retries * _poll_delay() >= async_s

    def test_custom_limit_is_followed(self):
        v = _long_exec_vars()
        retries = int(_render(v["rx_retries"], exec_long_async=7200))
        assert retries * _poll_delay() >= 7200
