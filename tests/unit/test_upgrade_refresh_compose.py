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


IMAGE_TAG = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml"
)


class TestImageTagGitopsDurable:
    """The compose image bump must persist to env.template, or the next gitops
    pull re-renders .env from it and reverts the upgrade. CANASTA_IMAGE is a
    plain literal in env.template (not a managed/secret placeholder key), so it
    must be updated directly — the config-set placeholder path skips it."""

    @staticmethod
    def _lineinfile(task):
        return (task.get("ansible.builtin.lineinfile")
                or task.get("lineinfile")) if isinstance(task, dict) else None

    def test_bump_updates_env_template_literal(self):
        tasks = _load(IMAGE_TAG)
        upd = next(
            (t for t in _iter_tasks(tasks)
             if self._lineinfile(t)
             and "env.template" in str(self._lineinfile(t).get("path", ""))),
            None,
        )
        assert upd is not None, (
            "upgrade_image_tag.yml must update the CANASTA_IMAGE literal in "
            "env.template so the bump survives the next gitops pull"
        )
        li = self._lineinfile(upd)
        assert "CANASTA_IMAGE" in str(li.get("regexp", ""))
        assert "canasta_default_image" in str(li.get("line", "")), (
            "the new env.template line must carry the upgrade's target tag"
        )
        assert "stat.exists" in str(upd.get("when", "")), (
            "the env.template update must be gated on .gitops-host existing"
        )

    def test_env_template_update_inherits_build_from_and_default_image_guards(self):
        # The env.template write must live INSIDE the guarded block so it never
        # clobbers a --build-from local build or a custom (non-default) image.
        block = next(
            t for t in _load(IMAGE_TAG)
            if isinstance(t, dict) and t.get("name") == "Update Compose image tag"
        )
        when = str(block.get("when", ""))
        assert "buildFrom" in when, "build-from instances must be excluded"
        assert "canastawiki/canasta:" in when, (
            "only the default Canasta image is managed; a custom image is left "
            "alone"
        )
        inside = [t for t in block.get("block", []) if self._lineinfile(t)]
        assert inside, (
            "the env.template update must be inside the guarded block so it "
            "inherits the build-from / default-image guards"
        )

    def test_bump_still_writes_env(self):
        # The .env write must remain — gitops persistence is in addition to it,
        # and non-gitops instances rely on the .env write alone.
        tasks = _load(IMAGE_TAG)
        env_writes = [
            t for t in _iter_tasks(tasks)
            if isinstance(t, dict) and "canasta_env" in t
            and t.get("canasta_env", {}).get("key") == "CANASTA_IMAGE"
        ]
        assert env_writes, "the .env CANASTA_IMAGE write must remain"

    @staticmethod
    def _lineinfile(task):
        return (task.get("ansible.builtin.lineinfile")
                or task.get("lineinfile")) if isinstance(task, dict) else None

    def test_k8s_bump_updates_gitops_image_tag(self):
        # K8s analog: on a gitops instance the upgrade must update
        # hosts/<host>/vars.yaml's image_tag, or render_kubernetes regenerates
        # the deployed values from the stale tag and reverts the bump.
        block = next(
            t for t in _load(IMAGE_TAG)
            if isinstance(t, dict)
            and t.get("name") == "Update Kubernetes image tag in values.yaml"
        )
        upd = next(
            (t for t in block.get("block", [])
             if self._lineinfile(t)
             and "image_tag" in str(self._lineinfile(t).get("regexp", ""))
             and "vars.yaml" in str(self._lineinfile(t).get("path", ""))),
            None,
        )
        assert upd is not None, (
            "K8s upgrade must update image_tag in hosts/<host>/vars.yaml so the "
            "bump survives a gitops pull"
        )
        assert "stat.exists" in str(upd.get("when", "")), (
            "the vars.yaml update must be gated on .gitops-host existing"
        )
