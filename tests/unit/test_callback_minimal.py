"""Unit tests for the canasta_minimal stdout callback plugin.

Regression coverage for the callback signature warnings (issue #572):
modern Ansible calls runner callback methods with a variable number of
positional arguments depending on context. Most notably, the meta-task
skip path calls ``v2_runner_on_skipped(host, task, utr)`` with three
positional args instead of a single ``result``. The plugin's methods
must accept that arity, or Ansible logs:

    [WARNING]: Callback dispatch 'v2_runner_on_skipped' failed for plugin
    'canasta_minimal': CallbackModule.v2_runner_on_skipped() takes 2
    positional arguments but 4 were given

These tests call each runner method with the variable-arity convention
directly, so they fail deterministically on the old fixed-arity
signatures regardless of the installed ansible-core version.
"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "callback_plugins")
)
import canasta_minimal  # noqa: E402


class _FakeDisplay:
    """Captures display() calls so tests can assert on output without
    writing to the real terminal."""

    def __init__(self):
        self.messages = []

    def display(self, msg, *args, **kwargs):
        self.messages.append(msg)

    def banner(self, *args, **kwargs):
        pass


class _FakeResult:
    """Minimal stand-in for a CallbackTaskResult: exposes the private
    ``_task``/``_result`` attributes the plugin reads."""

    def __init__(self, result=None, action="ansible.builtin.debug"):
        self._task = type("_FakeTask", (), {"action": action})()
        self._result = result or {}


def _make_cb():
    cb = canasta_minimal.CallbackModule()
    cb._display = _FakeDisplay()
    return cb


def test_on_skipped_accepts_meta_skip_arity():
    """The meta-task skip path calls v2_runner_on_skipped(host, task, utr)."""
    cb = _make_cb()
    # Must not raise TypeError; the old (self, result) signature did.
    cb.v2_runner_on_skipped("host", "task", "utr")


def test_runner_methods_accept_variable_arity():
    """Every runner callback must tolerate Ansible's extra positional/keyword
    args without raising TypeError."""
    cb = _make_cb()
    cb.v2_runner_on_start("host", "task", "extra")
    cb.v2_runner_on_skipped("host", "task", "utr")
    cb.v2_runner_on_ok(_FakeResult(result={"msg": "x"}), "task", "utr")
    cb.v2_runner_on_unreachable(_FakeResult(result={"msg": "down"}), "extra")
    cb.v2_runner_on_failed(_FakeResult(result={"msg": "boom"}), ignore_errors=True)
    cb.v2_runner_item_on_failed(_FakeResult(result={"msg": "item"}), "extra")


def test_on_ok_still_displays_debug_output():
    """Widening the signature must not stop debug-task output from showing."""
    cb = _make_cb()
    cb.v2_runner_on_ok(_FakeResult(result={"msg": "hello from debug"}))
    assert "hello from debug" in cb._display.messages


def test_on_failed_respects_ignore_errors_keyword():
    """ignore_errors arrives as a keyword from the strategy plugin; an
    ignored failure must produce no output."""
    cb = _make_cb()
    cb.v2_runner_on_failed(_FakeResult(result={"msg": "boom"}), ignore_errors=True)
    assert cb._display.messages == []


def test_on_failed_displays_error_when_not_ignored():
    cb = _make_cb()
    cb.v2_runner_on_failed(_FakeResult(result={"msg": "boom"}))
    assert any("boom" in m for m in cb._display.messages)
