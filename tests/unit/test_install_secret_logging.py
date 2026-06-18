"""Security-regression guard for wiki installation.

The install.php invocation in install_single_wiki.yml passes the root DB
password, the wiki DB password, and the admin password on the command
line. The orchestrator exec primitive only redacts the command (and the
task banner) when exec_no_log is true; without it the three secrets are
exposed in Ansible output on -v and on any install.php failure. See
issue #722.

This test parses install_single_wiki.yml and asserts that every exec
include whose command carries a *_password variable sets
exec_no_log: true.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INSTALL_WIKI = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "install_single_wiki.yml",
)

_PASSWORD_VAR = re.compile(r"_password\b")


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _exec_includes():
    """Yield the vars dict of every include_tasks that pulls the
    orchestrator exec primitive."""
    with open(INSTALL_WIKI) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        include = task.get("ansible.builtin.include_tasks") or \
            task.get("include_tasks")
        if not isinstance(include, str):
            continue
        if "orchestrator/tasks/exec.yml" in include:
            yield task.get("vars") or {}


class TestInstallSecretsNotLogged:
    """install.php carries three passwords; the exec MUST be no_log (#722)."""

    def test_install_php_exec_sets_no_log(self):
        found = False
        for vars_ in _exec_includes():
            command = str(vars_.get("exec_command", ""))
            if "install.php" in command:
                found = True
                assert vars_.get("exec_no_log") is True, (
                    "The install.php exec include must set "
                    "exec_no_log: true — it passes the root DB, wiki DB, "
                    "and admin passwords on the command line, which the "
                    "exec primitive otherwise logs in cleartext (#722)."
                )
        assert found, (
            "install_single_wiki.yml has no install.php exec include — "
            "the secret-logging regression guard cannot run."
        )

    def test_all_password_bearing_execs_are_no_log(self):
        """Any exec whose command references a *_password variable must
        be no_log, not just install.php."""
        for vars_ in _exec_includes():
            command = str(vars_.get("exec_command", ""))
            if _PASSWORD_VAR.search(command):
                assert vars_.get("exec_no_log") is True, (
                    "An exec include in install_single_wiki.yml references "
                    "a *_password variable without exec_no_log: true, "
                    "leaking the secret into Ansible output (#722). "
                    "Command: %s" % command
                )
