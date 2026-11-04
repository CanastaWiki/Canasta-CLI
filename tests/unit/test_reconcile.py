"""Structural guards for `canasta reconcile` — the non-disruptive converge
that brings an instance's config and runtime back into agreement (the fix
companion to `canasta doctor`).

It must regenerate config and converge via `start` (sync profiles + up -d) but
must NOT `stop` — that's what keeps it non-disruptive (no `down`).
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RECONCILE = os.path.join(
    REPO_ROOT, "roles", "instance_lifecycle", "tasks", "reconcile.yml"
)
PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "reconcile.yml")
COMMAND_DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _tasks_from(task):
    inc = task.get("ansible.builtin.include_role") or task.get("include_role")
    return inc.get("tasks_from", "") if isinstance(inc, dict) else ""


def _commands():
    data = _load(COMMAND_DEFS)
    return data["commands"] if isinstance(data, dict) else data


class TestReconcileTask:
    def test_converges_via_start_and_regenerates_config(self):
        froms = [_tasks_from(t) for t in _load(RECONCILE)]
        assert "update_config.yml" in froms, (
            "reconcile must regenerate rendered config (update_config.yml)"
        )
        assert "start.yml" in froms, (
            "reconcile must converge via start.yml (sync profiles + up -d)"
        )

    def test_is_non_disruptive_no_stop(self):
        froms = [_tasks_from(t) for t in _load(RECONCILE)]
        assert "stop.yml" not in froms, (
            "reconcile must NOT stop — it's the non-disruptive converge; a "
            "full down/up is `canasta restart`"
        )


class TestReconcileWiring:
    def test_playbook_includes_the_reconcile_role_task(self):
        froms = [_tasks_from(t) for t in _load(PLAYBOOK)]
        assert "reconcile.yml" in froms

    def test_command_is_defined_with_its_playbook(self):
        rec = next(
            (c for c in _commands() if c.get("name") == "reconcile"), None)
        assert rec is not None, "reconcile must be in command_definitions.yml"
        assert rec.get("playbook") == "reconcile.yml"


RECONCILE_TASKS = os.path.join(
    REPO_ROOT, "roles", "instance_lifecycle", "tasks", "reconcile.yml"
)


class TestReconcileLeavesTheImageToTheSync:
    """reconcile has no write of its own to env.template's CANASTA_IMAGE:
    copying .env's image there put one host's CLI pin into the shared
    file. An image the operator set in .env reaches the sources through
    the .env sync, which reconcile runs before the render."""

    def test_no_task_writes_canasta_image_to_env_template(self):
        def walk(tasks):
            for t in tasks or []:
                yield t
                yield from walk(t.get("block"))

        for t in walk(_load(RECONCILE_TASKS)):
            li = t.get("ansible.builtin.lineinfile") or {}
            assert not (str(li.get("path", "")).endswith("env.template")
                        and "CANASTA_IMAGE" in str(li.get("regexp", ""))), t

    def test_the_env_sync_runs_before_the_render(self):
        names = [t.get("name", "") for t in _load(RECONCILE_TASKS)]
        assert (names.index("Write .env edits into the gitops sources")
                < names.index("Render the gitops files from their sources"))
