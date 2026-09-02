"""Structural guards for `canasta upgrade` on an instance whose recorded
build-from source has gone missing — a deleted checkout, or a staging tree
under /tmp that the tool which created it cleaned up.

`playbooks/upgrade.yml` includes `_upgrade_single.yml` in a plain loop with no
per-instance rescue, and `canasta upgrade` cannot be scoped to one instance,
so a hard failure here strands every instance ordered after this one in the
registry. The rebuild is skipped with a warning instead, and the instance is
left running the image it already has rather than being repointed at a stock
one.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
UPGRADE_TAG = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml")
BUILD_FROM = os.path.join(
    REPO_ROOT, "roles", "imagebuild", "tasks", "build_from_source.yml")

STAT = "Check that the recorded build-from source still exists"
DECIDE = "Decide whether the image can be rebuilt from source"
WARN = "Warn that the recorded build-from source is gone"
REBUILD = "Rebuild image from source"
PUBLISH = "Publish rebuilt image for the orchestrator"


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _by_name(tasks, name):
    return next(t for t in tasks if t.get("name") == name)


def _names(tasks):
    return [t.get("name") for t in tasks]


def test_the_source_is_checked_before_the_rebuild():
    tasks = _tasks(UPGRADE_MAIN)
    names = _names(tasks)
    assert names.index(STAT) < names.index(DECIDE) < names.index(REBUILD)
    assert _by_name(tasks, STAT)["ansible.builtin.stat"]["path"].endswith(
        "/Canasta/Dockerfile")


def test_the_stat_is_unconditional():
    # A `when:` here would register a skip-result dict, which the upgrade
    # loop's next iteration would read as this instance's stat result.
    assert "when" not in _by_name(_tasks(UPGRADE_MAIN), STAT)


def test_the_rebuild_gate_requires_both_a_record_and_a_source():
    decide = _by_name(_tasks(UPGRADE_MAIN), DECIDE)
    expr = decide["ansible.builtin.set_fact"]["_upgrade_rebuild"]
    assert "_upgrade_build_from != ''" in expr
    assert "_upgrade_build_from_stat.stat.exists" in expr


def test_rebuild_and_publish_are_both_gated_on_the_source():
    tasks = _tasks(UPGRADE_MAIN)
    for name in (REBUILD, PUBLISH):
        assert _by_name(tasks, name)["when"] == "_upgrade_rebuild | bool"


def test_a_missing_source_warns_rather_than_failing():
    tasks = _tasks(UPGRADE_MAIN)
    warn = _by_name(tasks, WARN)
    assert "ansible.builtin.debug" in warn
    assert "not _upgrade_rebuild | bool" in warn["when"]
    # Nothing in the upgrade path may abort the loop over instances.
    assert not any("ansible.builtin.fail" in t for t in tasks)


def test_a_skipped_rebuild_does_not_force_a_restart():
    # The forced restart exists only to deploy a freshly built image; with no
    # rebuild there is nothing new to deploy.
    tasks = _tasks(UPGRADE_MAIN)
    expr = _by_name(tasks, "Decide whether a restart is needed")[
        "ansible.builtin.set_fact"]["_restart_needed"]
    assert "_upgrade_rebuild | bool" in expr
    assert "buildFrom" not in expr


def test_a_skipped_rebuild_does_not_repoint_the_instance_at_a_stock_image():
    # The tag bump stays gated on the registry record, not on whether the
    # rebuild ran — otherwise skipping the rebuild would swap the custom
    # image for a stock ghcr tag the instance was never built on.
    with open(UPGRADE_TAG) as f:
        txt = f.read()
    assert txt.count("buildFrom | default('') == ''") == 2


def test_create_still_fails_hard_on_a_missing_source():
    # `create` has no loop to strand and no image to fall back on, so the
    # loud failure belongs there.
    tasks = _tasks(BUILD_FROM)
    fail = _by_name(tasks, "Fail if Canasta source not found")
    assert "ansible.builtin.fail" in fail
