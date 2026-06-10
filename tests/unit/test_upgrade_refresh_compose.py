"""Guards for canasta upgrade refreshing the managed compose on existing
instances and recreating when only the compose changed.

write_stack_files is otherwise no-clobber, so a pre-existing instance never
received compose changes (e.g. a new crowdsec service behind a profile) on
upgrade, and the recreate only fired on an image bump. The fix makes the
managed docker-compose.yml force-refreshable via stack_files_force (upgrade
passes true) and registered, and makes upgrade recreate when the compose
changed, not just on an image change.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
WRITE_STACK = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "write_stack_files.yml"
)
UPGRADE_MAIN = os.path.join(
    REPO_ROOT, "roles", "upgrade", "tasks", "main.yml"
)


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _iter_tasks(tasks):
    """Yield every task, descending into block: wrappers."""
    for t in tasks:
        yield t
        if isinstance(t, dict) and "block" in t:
            yield from _iter_tasks(t["block"])


def _find(tasks, name_substring):
    for t in _iter_tasks(tasks):
        if isinstance(t, dict) and name_substring in t.get("name", ""):
            return t
    return None


class TestWriteStackFilesForceRefresh:
    def test_compose_copy_force_is_parameterized(self):
        copy = _find(_load(WRITE_STACK), "Copy docker-compose.yml")
        assert copy is not None
        force = copy["ansible.builtin.copy"]["force"]
        assert "stack_files_force" in str(force), (
            "docker-compose.yml must be force-refreshable via "
            "stack_files_force so upgrade can deliver compose changes to "
            "existing instances, not hardcoded force: false"
        )

    def test_compose_copy_is_registered(self):
        copy = _find(_load(WRITE_STACK), "Copy docker-compose.yml")
        assert copy.get("register") == "_compose_stack_copy", (
            "the docker-compose.yml copy must register its result so "
            "upgrade can detect a compose change and recreate"
        )

    def test_operator_adjacent_files_stay_no_clobber(self):
        override = _find(
            _load(WRITE_STACK), "Copy docker-compose.override.yml.example"
        )
        assert override["ansible.builtin.copy"]["force"] in (False, "false"), (
            "override example must stay no-clobber to preserve operator content"
        )


class TestUpgradeRefreshesAndRecreatesOnComposeChange:
    def test_upgrade_passes_stack_files_force_true(self):
        refresh = _find(_load(UPGRADE_MAIN), "Refresh stack files")
        assert refresh is not None
        assert refresh.get("vars", {}).get("stack_files_force") is True, (
            "upgrade must pass stack_files_force: true so the managed "
            "docker-compose.yml is refreshed on existing instances"
        )

    def test_restart_decision_accounts_for_compose_change(self):
        decide = _find(_load(UPGRADE_MAIN), "Decide whether a restart is needed")
        assert decide is not None, (
            "upgrade must compute a restart decision covering a compose-only "
            "change, not only an image bump"
        )
        expr = str(decide["ansible.builtin.set_fact"]["_restart_needed"])
        assert "_images_updated" in expr and "_compose_stack_copy" in expr, (
            "_restart_needed must be true on an image bump OR a compose change"
        )

    def test_restart_gated_on_restart_needed(self):
        restart = _find(_load(UPGRADE_MAIN), "Restart containers")
        assert "_restart_needed" in str(restart.get("when", "")), (
            "Restart must fire on _restart_needed so a compose-only change "
            "recreates the containers"
        )

    def test_already_up_to_date_gated_on_restart_needed(self):
        uptodate = _find(_load(UPGRADE_MAIN), "Report already up to date")
        assert "_restart_needed" in str(uptodate.get("when", "")), (
            "'Already up to date' must key off _restart_needed so a compose "
            "change isn't reported as a no-op"
        )
