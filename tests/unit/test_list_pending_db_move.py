"""`canasta list` flags an instance whose MediaWiki still waits to move off
the database administrator account, working it out from .env the way
`canasta upgrade` does (roles/upgrade/tasks/check_wiki_db_account.yml)."""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers  # noqa: E402

FLAG = "[DB ACCOUNT MOVE PENDING]"


class TestWikiDbMovePending:
    @pytest.mark.parametrize("env,k8s,pending", [
        ({}, False, False),
        ({"MYSQL_PASSWORD": "x"}, False, True),
        ({"WIKI_DB_USER": "root"}, False, True),
        ({"WIKI_DB_USER": ""}, False, True),
        ({"WIKI_DB_USER": "mediawiki"}, False, False),
        ({"WIKI_DB_USER": "mysite"}, False, False),
        ({"WIKI_DB_USER": "root", "CANASTA_DB_ROOT_ACCOUNT": "true"}, False, False),
        ({"WIKI_DB_USER": "root", "CANASTA_DB_ROOT_ACCOUNT": "TRUE"}, True, False),
        ({"WIKI_DB_USER": "root", "CANASTA_DB_ROOT_ACCOUNT": "false"}, False, True),
        ({"WIKI_DB_USER": "root"}, True, True),
        ({"WIKI_DB_USER": "mediawiki"}, True, False),
        # External database: the administrator is MYSQL_USER.
        ({"USE_EXTERNAL_DB": "true", "MYSQL_USER": "admin",
          "WIKI_DB_USER": "admin"}, False, True),
        ({"USE_EXTERNAL_DB": "true", "MYSQL_USER": "admin"}, False, True),
        ({"USE_EXTERNAL_DB": "true", "MYSQL_USER": "admin",
          "WIKI_DB_USER": "root"}, False, False),
        ({"USE_EXTERNAL_DB": "true", "WIKI_DB_USER": "root"}, False, True),
        # Kubernetes never moves an external database's account.
        ({"USE_EXTERNAL_DB": "true", "MYSQL_USER": "admin",
          "WIKI_DB_USER": "admin"}, True, False),
    ])
    def test_matches_the_upgrade_condition(self, env, k8s, pending):
        assert _helpers._wiki_db_move_pending(env, k8s) is pending

    def test_matches_the_ansible_check(self):
        # The Ansible side reads the same four keys.
        with open(os.path.join(REPO_ROOT, "roles", "upgrade", "tasks",
                               "check_wiki_db_account.yml")) as f:
            text = f.read()
        for key in _helpers._DB_ACCOUNT_KEYS:
            assert "variables.%s" % key in text


class TestDbAccountEnvGrep:
    def test_parses_only_the_account_keys(self):
        text = ("WIKI_DB_USER=root\n"
                "MYSQL_USER='admin'\n"
                "USE_EXTERNAL_DB=true\n"
                "CANASTA_DB_ROOT_ACCOUNT=false\n"
                "MYSQL_PASSWORD=secret\n")
        assert _helpers._parse_db_account_env(text) == {
            "WIKI_DB_USER": "root", "MYSQL_USER": "admin",
            "USE_EXTERNAL_DB": "true", "CANASTA_DB_ROOT_ACCOUNT": "false",
        }

    def test_grep_selects_no_secret(self, tmp_path):
        (tmp_path / ".env").write_text(
            "MYSQL_PASSWORD=secret\nWIKI_DB_PASSWORD=secret\n"
            "WIKI_DB_USER=root\nMW_SECRET_KEY=secret\n")
        out = subprocess.run(
            ["sh", "-c", _helpers._db_account_env_grep(
                _helpers._shell_quote(str(tmp_path)))],
            capture_output=True, text=True).stdout
        assert out == "WIKI_DB_USER=root\n"


def _instance(tmp_path, env, orchestrator="compose"):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wikis.yaml").write_text(yaml.safe_dump(
        {"wikis": [{"id": "main", "url": "m.example.com"}]}))
    (tmp_path / ".env").write_text(env)
    return {"path": str(tmp_path), "host": "localhost",
            "orchestrator": orchestrator}


class TestLocalList:
    @pytest.mark.parametrize("state", ["RUNNING", "STOPPED"])
    def test_flags_an_instance_on_root(self, tmp_path, monkeypatch, state):
        monkeypatch.setattr(_helpers, "_web_state", lambda i, inst: state)
        inst = _instance(tmp_path, "WIKI_DB_USER=root\n")
        detail = _helpers._gather_instance_info("mysite", inst)
        assert detail["status"] == "%s  %s" % (state, FLAG)

    def test_no_flag_once_moved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_helpers, "_web_state", lambda i, inst: "STOPPED")
        inst = _instance(tmp_path, "WIKI_DB_USER=mediawiki\n")
        assert _helpers._gather_instance_info(
            "mysite", inst)["status"] == "STOPPED"

    def test_k8s_external_database_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_helpers, "_web_state", lambda i, inst: "STOPPED")
        inst = _instance(tmp_path, "USE_EXTERNAL_DB=true\nWIKI_DB_USER=root\n",
                         orchestrator="kubernetes")
        assert _helpers._gather_instance_info(
            "mysite", inst)["status"] == "STOPPED"

    def test_missing_directory_not_flagged(self, tmp_path):
        inst = {"path": str(tmp_path / "gone"), "host": "localhost",
                "orchestrator": "compose"}
        assert _helpers._gather_instance_info(
            "mysite", inst)["status"] == "NOT FOUND"

    def test_printed_on_the_status_line(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_web_state", lambda i, inst: "STOPPED")
        inst = _instance(tmp_path, "WIKI_DB_USER=root\n")
        _helpers._print_table([_helpers._gather_instance_info("mysite", inst)])
        assert "  COMPOSE       STOPPED  %s\n" % FLAG in capsys.readouterr().out


class TestRemoteList:
    def _out(self, env_lines):
        d = _helpers._SENTINEL + "\n"
        return ("DIR_OK\n" + d + "WIKIS_MISSING\n" + d + d + d + d
                + env_lines)

    @pytest.mark.parametrize("env_lines,flagged", [
        ("WIKI_DB_USER=root\n", True),
        ("WIKI_DB_USER=mediawiki\n", False),
        ("WIKI_DB_USER=root\nCANASTA_DB_ROOT_ACCOUNT=true\n", False),
    ])
    def test_compose_reads_the_keys_in_the_batched_call(
            self, monkeypatch, env_lines, flagged):
        calls = []

        def fake_ssh(host, cmd, **kw):
            calls.append(cmd)
            return 0, self._out(env_lines)

        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        inst = {"path": "/srv/site", "orchestrator": "compose",
                "host": "remote"}
        detail = _helpers._gather_instance_info("site", inst)
        assert detail["status"] == (
            "STOPPED  " + FLAG if flagged else "STOPPED")
        assert len(calls) == 1
        assert _helpers._db_account_env_grep("'/srv/site'") in calls[0]

    def test_compose_unreachable_not_flagged(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_ssh_run", lambda h, c, **kw: (255, ""))
        inst = {"path": "/srv/site", "orchestrator": "compose",
                "host": "remote"}
        assert _helpers._gather_instance_info(
            "site", inst)["status"] == "UNREACHABLE"

    def _k8s(self, monkeypatch, rc, out):
        calls = []

        def fake_ssh(host, cmd, **kw):
            calls.append(cmd)
            return rc, out

        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        monkeypatch.setattr(_helpers, "_read_wikis", lambda p, h: [])
        monkeypatch.setattr(_helpers, "_web_state_k8s",
                            lambda i, h: "STOPPED")
        monkeypatch.setattr(_helpers, "_read_restore_marker",
                            lambda p, h: None)
        monkeypatch.setattr(_helpers, "_unfinished_pull", lambda p, h: None)
        return _helpers._gather_k8s("site", "/srv/site", "remote"), calls

    @pytest.mark.parametrize("out,flagged", [
        ("WIKI_DB_USER=root\n", True),
        ("WIKI_DB_USER=mediawiki\n", False),
        ("USE_EXTERNAL_DB=true\nWIKI_DB_USER=root\n", False),
    ])
    def test_k8s_reads_the_keys_with_the_directory_check(
            self, monkeypatch, out, flagged):
        detail, calls = self._k8s(monkeypatch, 0, out)
        assert detail["status"] == (
            "STOPPED  " + FLAG if flagged else "STOPPED")
        assert calls[0].startswith("test -d '/srv/site' || exit 1; ")
        assert _helpers._db_account_env_grep("'/srv/site'") in calls[0]

    def test_k8s_missing_directory_not_flagged(self, monkeypatch):
        detail, _ = self._k8s(monkeypatch, 1, "")
        assert detail["status"] == "NOT FOUND"
