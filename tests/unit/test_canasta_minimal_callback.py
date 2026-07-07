"""Tests for the canasta_minimal Ansible stdout callback."""

import os
import sys
from types import SimpleNamespace


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "callback_plugins"))

import canasta_minimal  # noqa: E402


def _make_callback():
    cb = canasta_minimal.CallbackModule()
    captured = []

    class _Display:
        def display(self, msg, color=None, stderr=False):
            captured.append((msg, color, stderr))

    cb._display = _Display()
    cb._captured = captured
    return cb


def _result(**fields):
    return SimpleNamespace(_result=dict(fields))


def _item_result(ignore_errors=None, **fields):
    """A loop-item result as Ansible delivers it to v2_runner_item_on_failed:
    the per-item _ansible_ignore_errors marker has already been consumed by
    loop aggregation, so the only reliable ignore_errors signal is the task
    object's own ignore_errors field."""
    return SimpleNamespace(
        _result=dict(fields),
        _task=SimpleNamespace(ignore_errors=ignore_errors),
    )


class TestFormatCmdDiagnostics:
    def test_empty(self):
        assert canasta_minimal.CallbackModule._format_cmd_diagnostics("", "") == ""

    def test_cmd_only_string(self):
        out = canasta_minimal.CallbackModule._format_cmd_diagnostics(
            "git-crypt --version", ""
        )
        assert out == " (cmd: git-crypt --version)"

    def test_cmd_only_list(self):
        out = canasta_minimal.CallbackModule._format_cmd_diagnostics(
            ["git-crypt", "--version"], ""
        )
        assert out == " (cmd: git-crypt --version)"

    def test_exception_picks_last_meaningful_line(self):
        tb = (
            "Traceback (most recent call last):\n"
            "  File \"/x.py\", line 1, in <module>\n"
            "    subprocess.run([\"git-crypt\"])\n"
            "FileNotFoundError: [Errno 2] No such file: 'git-crypt'\n"
            "\n"  # trailing blank line — must be skipped
        )
        out = canasta_minimal.CallbackModule._format_cmd_diagnostics("", tb)
        assert out == (
            " (FileNotFoundError: [Errno 2] No such file: 'git-crypt')"
        )

    def test_cmd_and_exception_combined(self):
        out = canasta_minimal.CallbackModule._format_cmd_diagnostics(
            ["git-crypt", "--version"],
            "FileNotFoundError: [Errno 2] No such file: 'git-crypt'",
        )
        assert out == (
            " (cmd: git-crypt --version; "
            "FileNotFoundError: [Errno 2] No such file: 'git-crypt')"
        )

    def test_traceback_unavailable_placeholder_suppressed(self):
        """Ansible sets exception='(traceback unavailable)' for tasks
        with no real traceback (notably ansible.builtin.fail). The
        callback must not surface that placeholder — without filtering,
        a fail task's user-facing error becomes 'Error: <msg>
        ((traceback unavailable))', which is meaningless noise."""
        out = canasta_minimal.CallbackModule._format_cmd_diagnostics(
            "", "(traceback unavailable)"
        )
        assert out == ""

    def test_traceback_unavailable_with_cmd_keeps_cmd(self):
        out = canasta_minimal.CallbackModule._format_cmd_diagnostics(
            ["git-crypt", "--version"], "(traceback unavailable)"
        )
        assert out == " (cmd: git-crypt --version)"

    def test_traceback_unavailable_in_multiline_skips_to_real_line(self):
        """Multi-line exception where the last line is the placeholder
        and the prior line is the real error: prefer the real error."""
        exc = (
            "FileNotFoundError: [Errno 2] No such file: 'git-crypt'\n"
            "(traceback unavailable)\n"
        )
        out = canasta_minimal.CallbackModule._format_cmd_diagnostics("", exc)
        assert out == (
            " (FileNotFoundError: [Errno 2] No such file: 'git-crypt')"
        )


class TestRunnerOnFailed:
    def test_oserror_on_spawn_surfaces_cmd_and_exception(self):
        cb = _make_callback()
        cb.v2_runner_on_failed(_result(
            msg="Error executing command.",
            cmd=["git-crypt", "--version"],
            exception=(
                "Traceback (most recent call last):\n"
                "FileNotFoundError: [Errno 2] No such file: 'git-crypt'"
            ),
            stdout="",
            stderr="",
        ))
        msgs = [m for (m, _, _) in cb._captured]
        assert any("Error executing command." in m for m in msgs)
        assert any("cmd: git-crypt --version" in m for m in msgs)
        assert any("FileNotFoundError" in m for m in msgs)

    def test_nonzero_return_code_path_unchanged(self):
        cb = _make_callback()
        cb.v2_runner_on_failed(_result(
            msg="non-zero return code",
            stdout="something failed on stdout",
            stderr="",
            cmd=["foo"],
        ))
        msgs = [m for (m, _, _) in cb._captured]
        assert msgs == ["Error: something failed on stdout"]

    def test_plain_msg_without_cmd_unchanged(self):
        cb = _make_callback()
        cb.v2_runner_on_failed(_result(msg="plain failure"))
        msgs = [m for (m, _, _) in cb._captured]
        assert msgs == ["Error: plain failure"]

    def test_fail_task_with_traceback_unavailable_placeholder(self):
        """End-to-end: ansible.builtin.fail sets exception=
        '(traceback unavailable)' alongside its real msg. The user
        should see only the real message, not the placeholder."""
        cb = _make_callback()
        cb.v2_runner_on_failed(_result(
            msg="Instance 'mwstake' not found in the registry or Kubernetes.",
            exception="(traceback unavailable)",
        ))
        msgs = [m for (m, _, _) in cb._captured]
        assert msgs == [
            "Error: Instance 'mwstake' not found in the registry or Kubernetes."
        ]

    def test_ignore_errors_suppresses_output(self):
        cb = _make_callback()
        cb.v2_runner_on_failed(
            _result(msg="Error executing command.", cmd=["x"]),
            ignore_errors=True,
        )
        assert cb._captured == []

    def test_one_or_more_items_failed_still_suppressed(self):
        cb = _make_callback()
        cb.v2_runner_on_failed(_result(msg="One or more items failed"))
        assert cb._captured == []


class TestRunnerItemOnFailed:
    def test_item_failure_displayed_when_not_ignored(self):
        cb = _make_callback()
        cb.v2_runner_item_on_failed(
            _item_result(ignore_errors=None, msg="missing required parameter")
        )
        msgs = [m for (m, _, _) in cb._captured]
        assert msgs == ["Error: missing required parameter"]

    def test_item_failure_displayed_when_no_task_attached(self):
        """Defensive: a result without a _task object (e.g. some param
        validation loops) must still surface its message."""
        cb = _make_callback()
        cb.v2_runner_item_on_failed(_result(msg="missing required parameter"))
        msgs = [m for (m, _, _) in cb._captured]
        assert msgs == ["Error: missing required parameter"]

    def test_item_failure_suppressed_via_task_ignore_errors(self):
        """How Ansible actually delivers it: the per-item
        _ansible_ignore_errors marker is consumed during loop aggregation
        and is absent from the item result, so the callback must read
        ignore_errors off the task. Otherwise the 'OK if dir already moved'
        mv in the upgrade directory-structure migration leaks a bare
        'Error: ...' line into the output."""
        cb = _make_callback()
        cb.v2_runner_item_on_failed(_item_result(
            ignore_errors=True,
            msg="non-zero return code",
        ))
        assert cb._captured == []

    def test_item_failure_suppressed_via_ignore_errors_kwarg(self):
        """Belt-and-suspenders for any ansible-core version that passes
        ignore_errors as a kwarg to the item callback."""
        cb = _make_callback()
        cb.v2_runner_item_on_failed(
            _result(msg="non-zero return code"), ignore_errors=True
        )
        assert cb._captured == []

    def test_item_command_failure_surfaces_stderr(self):
        """A looped command that exits non-zero (e.g. 'git add' on a path
        outside the repo) must surface the command's stderr, not just the
        opaque generic 'non-zero return code'."""
        cb = _make_callback()
        cb.v2_runner_item_on_failed(_item_result(
            ignore_errors=None,
            msg="non-zero return code",
            cmd=["git", "add", "--", "../wikis.yaml.template"],
            stdout="",
            stderr="fatal: '../wikis.yaml.template' is outside repository",
        ))
        msgs = [m for (m, _, _) in cb._captured]
        assert any("cmd: git add -- ../wikis.yaml.template" in m for m in msgs)
        assert any("outside repository" in m for m in msgs)

    def test_item_command_failure_surfaces_stdout(self):
        """When the command wrote the error to stdout, that is shown in
        place of the generic message (mirrors the task-level path)."""
        cb = _make_callback()
        cb.v2_runner_item_on_failed(_item_result(
            ignore_errors=None,
            msg="non-zero return code",
            cmd=["foo"],
            stdout="something failed on stdout",
            stderr="",
        ))
        msgs = [m for (m, _, _) in cb._captured]
        assert msgs == ["Error: something failed on stdout"]

    def test_item_plain_message_still_shown(self):
        """A non-command item failure (e.g. a fail task in a loop) with
        just a msg is unchanged: the message is displayed as-is."""
        cb = _make_callback()
        cb.v2_runner_item_on_failed(
            _item_result(ignore_errors=None, msg="missing required parameter")
        )
        msgs = [m for (m, _, _) in cb._captured]
        assert msgs == ["Error: missing required parameter"]


class TestRunnerOnOkDebug:
    """v2_runner_on_ok renders debug task 'msg' output. A list msg (one
    element per line) must render line-by-line, not as a Python list repr.
    """

    def _debug_result(self, msg):
        return SimpleNamespace(
            _result={"msg": msg},
            _task=SimpleNamespace(action="ansible.builtin.debug"),
        )

    def test_list_msg_rendered_line_by_line(self):
        cb = _make_callback()
        cb.v2_runner_on_ok(self._debug_result(["line one", "line two", ""]))
        assert len(cb._captured) == 1
        shown = cb._captured[0][0]
        assert shown == "line one\nline two\n"
        assert "[" not in shown and "'" not in shown

    def test_string_msg_unchanged(self):
        cb = _make_callback()
        cb.v2_runner_on_ok(self._debug_result("just a string"))
        assert cb._captured[0][0] == "just a string"

    def test_non_debug_task_suppressed(self):
        cb = _make_callback()
        result = SimpleNamespace(
            _result={"msg": ["x", "y"]},
            _task=SimpleNamespace(action="ansible.builtin.command"),
        )
        cb.v2_runner_on_ok(result)
        assert cb._captured == []
