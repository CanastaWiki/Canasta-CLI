"""Tests for the canasta_minimal Ansible stdout callback."""

import os
import sys
from types import SimpleNamespace

import pytest

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
