"""Structural guards on playbooks/export.yml's database dump."""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
EXPORT = os.path.join(REPO_ROOT, "playbooks", "export.yml")


def _tasks():
    with open(EXPORT) as f:
        return [t for t in (yaml.safe_load(f) or []) if isinstance(t, dict)]


def _dump_command():
    cmds = [
        (t.get("vars") or {}).get("exec_command", "")
        for t in _tasks()
    ]
    dumps = [c for c in cmds if "mariadb-dump" in c]
    assert len(dumps) == 1, "export.yml must run exactly one mariadb-dump"
    return dumps[0]


class TestDumpPassword:
    def test_password_not_on_command_line(self):
        cmd = _dump_command()
        assert "-p" not in cmd.split(), cmd
        assert '-p"' not in cmd and "-p'" not in cmd and "-p$" not in cmd, (
            "mariadb-dump must not receive the password as an argument, "
            "where it is visible in the process list: %r" % cmd)

    def test_password_passed_via_mysql_pwd(self):
        assert 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"' in _dump_command()
