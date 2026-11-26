"""`canasta list` works out a pending database account move on Kubernetes.

A Kubernetes instance never moves an external database's account, honors
CANASTA_DB_ROOT_ACCOUNT, and on a remote host reads the account keys in the
same call that checks the instance directory exists.
"""

import pytest

from test_list_pending_db_move import FLAG, _helpers, _instance


class TestWikiDbMovePending:
    @pytest.mark.parametrize("env,k8s,pending", [
        ({"WIKI_DB_USER": "root", "CANASTA_DB_ROOT_ACCOUNT": "TRUE"}, True, False),
        ({"WIKI_DB_USER": "root"}, True, True),
        ({"WIKI_DB_USER": "mediawiki"}, True, False),
        # Kubernetes never moves an external database's account.
        ({"USE_EXTERNAL_DB": "true", "MYSQL_USER": "admin",
          "WIKI_DB_USER": "admin"}, True, False),
    ])
    def test_matches_the_upgrade_condition(self, env, k8s, pending):
        assert _helpers._wiki_db_move_pending(env, k8s) is pending


class TestLocalList:
    def test_k8s_external_database_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_helpers, "_web_state", lambda i, inst: "STOPPED")
        inst = _instance(tmp_path, "USE_EXTERNAL_DB=true\nWIKI_DB_USER=root\n",
                         orchestrator="kubernetes")
        assert _helpers._gather_instance_info(
            "mysite", inst)["status"] == "STOPPED"


class TestRemoteList:
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
