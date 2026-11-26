"""MediaWiki's database account on Kubernetes.

ensure_wiki_db_account.sh runs in an init container beside the database
rather than in it, so with DB_HOST set every login it makes goes over TCP to
that host. The Ansible ensure step is skipped on Kubernetes, and create
picks the account there as it does on Compose.
"""

import pytest
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _orchestrator import TEMPLAR_VARS
from test_wiki_db_account import (  # noqa: F401
    EXTERNAL, _ensure_needed_expr, _wiki_db_user_expr, db)


class TestTheScript:
    def test_db_host_sends_every_login_over_tcp(self, db, tmp_path):  # noqa: F811
        # The Kubernetes init container runs beside the database, not in it.
        result, _ = db("main", "main_cargo", db_host="db")
        assert result.returncode == 0, result.stderr
        calls = (tmp_path / "calls").read_text().splitlines()
        assert calls and all(c == "-h db" for c in calls), calls
        assert "pw" in (tmp_path / "logins").read_text().split()


class TestWiring:
    @pytest.mark.parametrize("env, orch, needed", [
        ({"WIKI_DB_USER": "mediawiki_site", **EXTERNAL}, "kubernetes", False),
        ({"WIKI_DB_USER": "mediawiki"}, "kubernetes", False),
    ])
    def test_skipped_on_kubernetes(
            self, env, orch, needed):
        templar = Templar(loader=DataLoader(), variables={
            "_ensure_env": {"variables": env},
            "instance_orchestrator": orch, **TEMPLAR_VARS})
        assert templar.template(trust_as_template(_ensure_needed_expr())) is needed, (
            env, orch)

    @pytest.mark.parametrize("given, orch, expected", [
        ("", "kubernetes", "mediawiki"),
        ("wiki", "kubernetes", "wiki"),
    ])
    def test_create_defaults_to_the_mediawiki_account(self, given, orch, expected):
        templar = Templar(loader=DataLoader(), variables={
            "wikidbuser": given, "orchestrator": orch, **TEMPLAR_VARS})
        assert templar.template(trust_as_template(_wiki_db_user_expr())) == expected
