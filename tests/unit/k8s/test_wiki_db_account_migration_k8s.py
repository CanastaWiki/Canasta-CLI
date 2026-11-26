"""upgrade's decision to move a Kubernetes instance onto MediaWiki's own
database account: a running instance on root is moved, an external database
is left on its administrator account, and the opt-outs that hold on Compose
hold here too."""

import pytest

from test_wiki_db_account_migration import (
    CHECK, MIGRATE, TEMPLAR_VARS, _load, _named, _render)


class TestWhoIsMoved:
    def _decide(self, env, orchestrator="compose", running=True):
        check = _named(_load(CHECK),
                       "Decide whether MediaWiki waits to move to its own database account")
        # The task's vars are templated first, as Ansible does.
        variables = {"_mig_db_env": {"variables": env},
                     "instance_orchestrator": orchestrator, **TEMPLAR_VARS}
        for name, expr in check["vars"].items():
            variables[name] = _render(expr, **variables)
        pending = _render(check["ansible.builtin.set_fact"]["_mig_db_pending"],
                          **variables)
        task = _named(_load(MIGRATE),
                      "Decide whether to move MediaWiki to its own database account")
        return _render(task["ansible.builtin.set_fact"]["_mig_db_account"],
                       _mig_db_pending=pending,
                       instance_orchestrator=orchestrator, _was_running=running,
                       **TEMPLAR_VARS)

    @pytest.mark.parametrize("orchestrator", ["kubernetes"])
    def test_a_running_root_instance_is_moved(self, orchestrator):
        assert self._decide({"WIKI_DB_USER": "root"}, orchestrator) is True
        assert self._decide({}, orchestrator) is True

    EXTERNAL = {"USE_EXTERNAL_DB": "true", "MYSQL_USER": "op"}

    def test_an_external_kubernetes_instance_is_left_alone(self):
        assert self._decide({"WIKI_DB_USER": "op", **self.EXTERNAL},
                            "kubernetes") is False

    @pytest.mark.parametrize("orchestrator", ["kubernetes"])
    @pytest.mark.parametrize("env, running", [
        ({"WIKI_DB_USER": "mediawiki"}, True),
        ({"WIKI_DB_USER": "root", "CANASTA_DB_ROOT_ACCOUNT": "true"}, True),
        ({"WIKI_DB_USER": "op", "MYSQL_USER": "op", "USE_EXTERNAL_DB": "true",
          "CANASTA_DB_ROOT_ACCOUNT": "true"}, True),
        ({"WIKI_DB_USER": "root"}, False),
    ])
    def test_others_are_left_alone(self, env, orchestrator, running):
        assert self._decide(env, orchestrator, running) is False
