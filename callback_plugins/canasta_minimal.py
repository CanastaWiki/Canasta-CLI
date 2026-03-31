# -*- coding: utf-8 -*-

"""Minimal callback plugin for Canasta-Ansible.

Suppresses all Ansible framework output and only displays:
- Messages from ansible.builtin.debug tasks (the command's actual output)
- Error/failure messages
- Fatal messages

This gives the wrapper script a clean CLI experience.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = """
    name: canasta_minimal
    type: stdout
    short_description: Minimal output for Canasta CLI
    description:
        - Only shows debug messages and errors.
"""

import sys

from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "canasta_minimal"

    def __init__(self):
        super().__init__()
        self._last_task_name = None

    # --- Suppress everything by default ---

    def v2_playbook_on_play_start(self, play):
        pass

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._last_task_name = task.get_name()

    def v2_playbook_on_handler_task_start(self, task):
        pass

    def v2_playbook_on_include(self, included_file):
        pass

    def v2_playbook_on_stats(self, stats):
        # Show nothing on success; on failure the error was already printed
        pass

    def v2_runner_on_start(self, host, task):
        pass

    def v2_runner_on_ok(self, result):
        # Only show output from debug tasks (filter Ansible internal messages)
        if result._task.action in ("ansible.builtin.debug", "debug"):
            msg = result._result.get("msg")
            if msg and msg not in ("All items completed", "All items skipped"):
                self._display.display(str(msg))

    def v2_runner_on_skipped(self, result):
        pass

    def v2_runner_on_unreachable(self, result):
        self._display.display(
            "ERROR: Host unreachable: %s" % result._result.get("msg", ""),
            color="red",
            stderr=True,
        )

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if ignore_errors:
            return
        msg = result._result.get("msg", "")
        stderr = result._result.get("stderr", "")
        stdout = result._result.get("stdout", "")
        # For command failures, msg is generic "non-zero return code"
        # but stdout/stderr have the actual error from the command.
        if stdout and msg and "non-zero return code" in msg:
            self._display.display("Error: %s" % stdout.strip(), color="red", stderr=True)
        elif msg:
            self._display.display("Error: %s" % msg, color="red", stderr=True)
        if stderr:
            self._display.display(stderr, color="red", stderr=True)

    def v2_playbook_on_no_hosts_matched(self):
        self._display.display("Error: No matching hosts found", color="red", stderr=True)
