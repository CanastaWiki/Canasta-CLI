"""upgrade moves existing instances onto MediaWiki's own account.

The move runs after upgrade's restart, only for a running instance that
still uses the database administrator account (root on the bundled
database, MYSQL_USER on an external one, which is moved on Compose only)
and has not opted out. It refuses when a settings file sets
$wgDBuser/$wgDBpassword or the server holds a database no wiki declares, and
rolls back to the administrator account if anything fails after the switch.
CANASTA_DB_ROOT_ACCOUNT=true is the opt-out, and it also puts a moved
instance back on root at its next start.
"""

import os
import sys

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers  # noqa: E402

from _orchestrator import TEMPLAR_VARS

UPGRADE = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks")
MIGRATE = os.path.join(UPGRADE, "migrate_wiki_db_account.yml")
CHECK = os.path.join(UPGRADE, "check_wiki_db_account.yml")
DEFAULTS = os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml")
CONFIG_SET = os.path.join(REPO_ROOT, "roles", "config", "tasks", "set.yml")
SWITCH = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                      "switch_wiki_db_account.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _named(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
        for key in ("block", "rescue", "always"):
            found = _named(task.get(key) or [], name) if task.get(key) else None
            if found:
                return found
    return None


def _render(expr, **variables):
    return Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(expr))


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

    @pytest.mark.parametrize("orchestrator", ["compose", "kubernetes"])
    def test_a_running_root_instance_is_moved(self, orchestrator):
        assert self._decide({"WIKI_DB_USER": "root"}, orchestrator) is True
        assert self._decide({}, orchestrator) is True

    EXTERNAL = {"USE_EXTERNAL_DB": "true", "MYSQL_USER": "op"}

    def test_an_external_compose_instance_on_its_administrator_is_moved(self):
        assert self._decide({"WIKI_DB_USER": "op", **self.EXTERNAL}) is True
        assert self._decide(dict(self.EXTERNAL)) is True

    def test_an_external_kubernetes_instance_is_left_alone(self):
        assert self._decide({"WIKI_DB_USER": "op", **self.EXTERNAL},
                            "kubernetes") is False

    def test_an_external_instance_with_its_own_account_is_left_alone(self):
        assert self._decide({"WIKI_DB_USER": "mediawiki_site",
                             **self.EXTERNAL}) is False

    @pytest.mark.parametrize("orchestrator", ["compose", "kubernetes"])
    @pytest.mark.parametrize("env, running", [
        ({"WIKI_DB_USER": "mediawiki"}, True),
        ({"WIKI_DB_USER": "root", "CANASTA_DB_ROOT_ACCOUNT": "true"}, True),
        ({"WIKI_DB_USER": "op", "MYSQL_USER": "op", "USE_EXTERNAL_DB": "true",
          "CANASTA_DB_ROOT_ACCOUNT": "true"}, True),
        ({"WIKI_DB_USER": "root"}, False),
    ])
    def test_others_are_left_alone(self, env, orchestrator, running):
        assert self._decide(env, orchestrator, running) is False

    def test_the_staged_flag_is_reset_for_every_instance(self):
        task = _named(_load(MIGRATE),
                      "Decide whether to move MediaWiki to its own database account")
        assert "when" not in task
        assert task["ansible.builtin.set_fact"]["_mig_db_staged"] is False
        assert task["ansible.builtin.set_fact"]["_mig_db_redeployed"] is False


class TestRefusals:
    def test_undeclared_databases_are_found(self):
        task = _named(_load(MIGRATE), "Find databases no wiki declares")
        undeclared = _render(
            task["ansible.builtin.set_fact"]["_mig_db_undeclared"],
            _mig_db_list={"stdout_lines": ["information_schema", "main",
                                           "main_cargo", "mysql", "stray", "sys"]},
            _mig_db_wikis={"db_groups": [{"wiki": "main",
                                          "databases": ["main", "main_cargo"]}]})
        assert undeclared == ["stray"]

    def test_switch_happens_only_without_refusals(self):
        switch = _named(_load(MIGRATE), "Switch to MediaWiki's own account")
        assert "_mig_db_overrides.stdout_lines | length == 0" in switch["when"]
        assert "_mig_db_undeclared | length == 0" in switch["when"]
        assert switch["ansible.builtin.include_tasks"].strip().endswith(
            "/roles/orchestrator/tasks/switch_wiki_db_account.yml")

    def test_databases_are_listed_where_db_admin_says(self):
        # On Kubernetes db_admin.yml points _db_admin_exec at the db pod.
        names = [t.get("name") for t in _named(
            _load(MIGRATE), "Move MediaWiki to its own database account")["block"]]
        assert names.index("Find where database administration runs") < \
            names.index("List the databases on the server")

    def test_settings_file_overrides_are_looked_for(self):
        look = _named(_load(MIGRATE), "Look for database credentials set in settings files")
        assert any("wgDB(user|password)" in a
                   for a in look["ansible.builtin.command"]["argv"])
        assert look["failed_when"] == "_mig_db_overrides.rc > 1"


class TestSwitchAndRollback:
    def _switch(self):
        return _named(_load(SWITCH), "Switch (Docker Compose)")

    def test_keys_are_recorded_through_config_set(self):
        names = [t["name"] for t in self._switch()["block"]]
        record = self._switch()["block"][names.index("Record the account in .env")]
        assert record["ansible.builtin.include_role"] == {
            "name": "config", "tasks_from": "set.yml"}
        assert "WIKI_DB_USER={{ _mig_db_user }}" in record["vars"]["config_settings_override"]
        choose = self._switch()["block"][names.index("Choose the account")]
        assert "canasta_wiki_db_user(_mig_db_external | bool)" in (
            choose["ansible.builtin.set_fact"]["_mig_db_user"])
        assert names.index("Choose the account") < names.index("Record the account in .env")
        assert record["vars"]["no_restart"] is True
        assert (names.index("Record the account in .env")
                < names.index("Restart onto the new account")
                < names.index("Check that each wiki reaches its database as the new account"))

    def test_a_failure_rolls_back_to_the_administrator_and_restarts(self):
        rescue = self._switch()["rescue"]
        names = [t["name"] for t in rescue]
        roll = rescue[names.index("Roll back to the administrator account")]
        assert roll["vars"]["config_settings_override"] == (
            "WIKI_DB_USER={{ _mig_db_admin_user }}")
        assert names.index("Roll back to the administrator account") < \
            names.index("Restart on the administrator account")

    def test_config_set_does_not_advise_reconcile_before_its_own_restart(self):
        switch = self._switch()
        for tasks, name in ((switch["block"], "Record the account in .env"),
                            (switch["rescue"], "Roll back to the administrator account")):
            assert _named(tasks, name)["vars"]["config_restart_by_caller"] is True

    @pytest.mark.parametrize("no_restart, by_caller, advised", [
        (True, False, True),
        (True, True, False),
        (False, False, False),
    ])
    def test_reconcile_advice(self, no_restart, by_caller, advised):
        report = _named(_load(CONFIG_SET), "Report settings applied")
        variables = {"no_restart": no_restart, "_container_running": True}
        if by_caller:
            variables["config_restart_by_caller"] = True
        msg = _render(report["ansible.builtin.debug"]["msg"], **variables)
        assert ("canasta reconcile" in msg) is advised

    def test_runs_after_upgrades_restart(self):
        names = [t.get("name") for t in _load(os.path.join(UPGRADE, "main.yml"))]
        assert names.index("Restart containers") < \
            names.index("Move MediaWiki to its own database account")


class TestOptOut:
    def test_the_key_is_known(self):
        names = [k["name"] for k in _load(DEFAULTS)["canasta_known_keys"]]
        assert "CANASTA_DB_ROOT_ACCOUNT" in names
        assert "WIKI_DB_PASSWORD" in names
        assert "WIKI_DB_USER" in names

    def test_opting_out_puts_a_moved_instance_back_on_root(self):
        env = {"MYSQL_USER": "root", "MYSQL_PASSWORD": "rootpw",
               "WIKI_DB_USER": "mediawiki", "WIKI_DB_PASSWORD": "wikipw",
               "CANASTA_DB_ROOT_ACCOUNT": "true"}
        assert _helpers._wiki_db_account_updates(env) == [
            ("WIKI_DB_USER", "root"), ("WIKI_DB_PASSWORD", "rootpw")]

    def test_without_the_key_a_moved_instance_is_kept(self):
        env = {"MYSQL_USER": "root", "MYSQL_PASSWORD": "rootpw",
               "WIKI_DB_USER": "mediawiki", "WIKI_DB_PASSWORD": "wikipw"}
        assert _helpers._wiki_db_account_updates(env) == []


class TestRestoreKeepsThisHostsAccount:
    def test_compose_restore_puts_back_the_wiki_account(self):
        path = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                            "restore_instance.yml")
        task = _named(_load(path),
                      "Preserve MediaWiki's database account in restored .env")
        assert task["loop"] == ["WIKI_DB_USER", "WIKI_DB_PASSWORD"]
        assert "wiki is not defined" in task["when"]
        assert task["no_log"] is True
        restore = _load(os.path.join(REPO_ROOT, "roles", "backup", "tasks",
                                     "restore.yml"))
        names = [t.get("name") for t in restore]
        assert (names.index("Save MediaWiki's database account before restore")
                < names.index("Restore from snapshot"))
