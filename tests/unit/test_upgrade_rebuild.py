"""Static guards for the canasta-upgrade rebuild flow (#562).

The rebuild step must run when any image change is detected and must
cover every buildable service (operator override or devmode xdebug),
not just hardcode `web`. Both gates were the original gaps in #562.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
UPGRADE_MAIN = os.path.join(
    REPO_ROOT, "roles", "upgrade", "tasks", "main.yml"
)


def _load_tasks():
    with open(UPGRADE_MAIN) as f:
        return yaml.safe_load(f)


def _find_task(tasks, name_substring):
    for t in tasks:
        if name_substring in t.get("name", ""):
            return t
    return None


class TestDynamicBuildableServiceDetection:
    def test_enumerates_buildable_services_via_compose_config(self):
        tasks = _load_tasks()
        names = [t.get("name", "") for t in tasks]
        assert any(
            "Enumerate buildable services" in n for n in names
        ), (
            "upgrade must enumerate services with a build: directive "
            "rather than hardcoding `web` (#562, gap 2)"
        )

    def test_uses_merged_compose_config_json(self):
        with open(UPGRADE_MAIN) as f:
            content = f.read()
        assert "config --format json" in content, (
            "Buildable-service detection must use `docker compose "
            "config --format json` so the main + override + dev "
            "merge is what's queried, not just the override file"
        )

    def test_extract_uses_build_attribute(self):
        with open(UPGRADE_MAIN) as f:
            content = f.read()
        assert "selectattr('value.build', 'defined')" in content, (
            "Must filter merged services by presence of a build: key"
        )


class TestRebuildFiresOnAnyImageChange:
    def test_rebuild_task_is_top_level_not_nested(self):
        """The rebuild step previously lived inside a block gated on
        `not _images_updated`. Lifting it out is the whole point of
        gap 1 — otherwise pull-bumped non-buildable services suppress
        the buildable-service rebuild."""
        tasks = _load_tasks()
        rebuild = _find_task(tasks, "Rebuild buildable services")
        assert rebuild is not None, (
            "Top-level 'Rebuild buildable services' task must exist"
        )
        # If the task is at top level, it has no 'block' wrapper key
        # appearing as one of the keys above it. Check that the task
        # itself is a leaf command, not a block wrapper.
        assert "block" not in rebuild
        assert rebuild.get("ansible.builtin.command") is not None

    def test_rebuild_conditions(self):
        tasks = _load_tasks()
        rebuild = _find_task(tasks, "Rebuild buildable services")
        when = rebuild.get("when", [])
        if isinstance(when, str):
            when = [when]
        joined = " ".join(when)
        assert "_images_updated" in joined, (
            "Rebuild must be gated on _images_updated"
        )
        assert "_buildable_services" in joined, (
            "Rebuild must be gated on there being buildable services "
            "(no-op if no override / devmode adds one)"
        )

    def test_rebuild_iterates_buildable_services(self):
        tasks = _load_tasks()
        rebuild = _find_task(tasks, "Rebuild buildable services")
        loop = rebuild.get("loop", "")
        assert "_buildable_services" in str(loop), (
            "Rebuild must loop over _buildable_services, not hardcode "
            "the `web` service (#562, gap 2)"
        )

    def test_running_vs_configured_check_still_gated_on_not_updated(self):
        """The running-vs-configured fallback only needs to fire when
        pull didn't already detect an update. Its gate is unchanged
        from the original logic and must stay."""
        tasks = _load_tasks()
        check = _find_task(tasks, "Check running container image vs configured")
        when = check.get("when", [])
        if isinstance(when, str):
            when = [when]
        joined = " ".join(when)
        assert "not (_images_updated | bool)" in joined or "not _images_updated" in joined
        assert "_buildable_services" in joined, (
            "Fallback check is only meaningful when there's a "
            "buildable service to potentially rebuild"
        )
