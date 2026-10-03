"""Security-regression guard for wiki installation.

run_install_php.yml hands install.php the DB and admin passwords through
files staged from the exec's stdin, so neither reaches a command line
(host or container) or the async job file of the long exec. The
orchestrator exec primitive only redacts the command (and the task
banner) when exec_no_log is true, so every exec that touches a password
must still set it. See issues #722 and #1698.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INSTALL_WIKI = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "install_single_wiki.yml",
)
RUN_INSTALL_PHP = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "run_install_php.yml",
)
ADD_PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "add.yml")
EXTRACT_SECRET_KEY = os.path.join(
    REPO_ROOT, "roles", "upgrade", "tasks", "migrations",
    "extract_secret_key.yml",
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


def _exec_includes(path):
    """Yield the vars dict of every include_tasks that pulls the
    orchestrator exec primitive."""
    with open(path) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        include = task.get("ansible.builtin.include_tasks") or \
            task.get("include_tasks")
        if isinstance(include, dict):
            include = include.get("file")
        if not isinstance(include, str):
            continue
        if "orchestrator/tasks/exec.yml" in include:
            yield task.get("vars") or {}


class TestInstallSecretsNotLogged:
    """install.php handles two passwords; the exec MUST be no_log (#722)."""

    def test_install_php_exec_sets_no_log(self):
        found = False
        for vars_ in _exec_includes(RUN_INSTALL_PHP):
            command = str(vars_.get("exec_command", ""))
            if "install.php" in command:
                found = True
                assert vars_.get("exec_no_log") is True, (
                    "The install.php exec include must set "
                    "exec_no_log: true — it handles the DB and admin "
                    "passwords, which the exec primitive otherwise logs "
                    "in cleartext (#722)."
                )
        assert found, (
            "run_install_php.yml has no install.php exec include — "
            "the secret-logging regression guard cannot run."
        )

    def test_install_php_reads_passwords_from_files(self):
        for vars_ in _exec_includes(RUN_INSTALL_PHP):
            command = str(vars_.get("exec_command", ""))
            if "install.php" not in command:
                continue
            assert "--dbpassfile=" in command
            assert "--passfile=" in command
            assert not _PASSWORD_VAR.search(command), command

    def test_password_files_are_staged_from_stdin(self):
        staged = [v for v in _exec_includes(RUN_INSTALL_PHP)
                  if v.get("exec_stdin")]
        assert len(staged) == 1
        assert staged[0].get("exec_no_log") is True
        assert "umask 077" in staged[0]["exec_command"]
        assert not _PASSWORD_VAR.search(staged[0]["exec_command"])

    def test_callers_use_run_install_php(self):
        for path in (INSTALL_WIKI, ADD_PLAYBOOK):
            with open(path) as f:
                text = f.read()
            assert "run_install_php.yml" in text, path
            assert "maintenance/install.php" not in text, path

    def test_all_password_bearing_execs_are_no_log(self):
        """Any exec whose command references a *_password variable must
        be no_log, not just install.php."""
        for vars_ in (list(_exec_includes(INSTALL_WIKI))
                      + list(_exec_includes(RUN_INSTALL_PHP))):
            command = str(vars_.get("exec_command", ""))
            if _PASSWORD_VAR.search(command):
                assert vars_.get("exec_no_log") is True, (
                    "An exec include in the wiki install references "
                    "a *_password variable without exec_no_log: true, "
                    "leaking the secret into Ansible output (#722). "
                    "Command: %s" % command
                )

    def test_env_read_all_is_no_log(self):
        """The read_all of .env pulls MYSQL_PASSWORD into a registered
        variable; the task must be no_log."""
        with open(INSTALL_WIKI) as f:
            tasks = yaml.safe_load(f)
        found = False
        for task in _walk_tasks(tasks):
            env = task.get("canasta_env")
            if isinstance(env, dict) and env.get("state") == "read_all":
                found = True
                assert task.get("no_log") is True, (
                    "The canasta_env read_all in install_single_wiki.yml "
                    "must set no_log: true — it registers the full .env, "
                    "including MYSQL_PASSWORD."
                )
        assert found, (
            "install_single_wiki.yml has no canasta_env read_all task — "
            "the .env secret-logging guard cannot run."
        )


class TestExtractSecretKeyNotLogged:
    """extract_secret_key.yml handles $wgSecretKey, the wiki's
    session-signing secret; every task carrying it must be no_log."""

    def _tasks(self):
        with open(EXTRACT_SECRET_KEY) as f:
            return list(_walk_tasks(yaml.safe_load(f)))

    def test_grep_register_is_no_log(self):
        """The shell grep registers the extracted secret."""
        found = False
        for task in self._tasks():
            if task.get("register") == "_sk_grep":
                found = True
                assert task.get("no_log") is True, (
                    "The wgSecretKey grep task must set no_log: true — "
                    "it registers the wiki's session-signing secret."
                )
        assert found, "extract_secret_key.yml has no _sk_grep register task."

    def test_secret_saves_are_no_log(self):
        """Every canasta_env set of MW_SECRET_KEY must be no_log."""
        found = False
        for task in self._tasks():
            env = task.get("canasta_env")
            if isinstance(env, dict) and env.get("key") == "MW_SECRET_KEY":
                found = True
                assert task.get("no_log") is True, (
                    "A canasta_env task saving MW_SECRET_KEY in "
                    "extract_secret_key.yml must set no_log: true."
                )
        assert found, (
            "extract_secret_key.yml has no MW_SECRET_KEY save task."
        )
