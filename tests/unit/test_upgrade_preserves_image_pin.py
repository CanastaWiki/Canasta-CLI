"""An explicitly chosen image tag is the operator's, not the CLI's.

The rewrite exists to move an instance off the version tag a previous
CLI pinned. Its guard tested the repository and inequality only:

    CANASTA_IMAGE is match('^ghcr\\.io/canastawiki/canasta:')
    CANASTA_IMAGE != canasta_default_image

`ghcr.io/canastawiki/canasta:latest` satisfies both — it lives under the
official repository and never equals a pinned version — so every upgrade
rewrote it back to a fixed version, and repeating the change never made
it stick. Nothing reported the change either.

A version-shaped tag is what distinguishes a pin the CLI wrote from one
the operator chose.
"""

import os
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "roles", "common", "module_utils"))
from canasta_image_pin import retarget_release_image

TASKS = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml")

DEFAULT = "ghcr.io/canastawiki/canasta:3.5.20"


def _tasks():
    with open(TASKS) as f:
        return yaml.safe_load(f)


def _compose_task():
    for task in _tasks():
        if task.get("name") == "Update Compose image tag":
            return task
    raise AssertionError("Update Compose image tag not found")


def _target_expression():
    for task in _tasks():
        facts = task.get("ansible.builtin.set_fact") or {}
        if "_upgrade_compose_target" in facts:
            return facts["_upgrade_compose_target"]
    raise AssertionError("_upgrade_compose_target not set")


def _would_rewrite(value):
    """Evaluate the guard's value-dependent conditions."""
    assert "canasta_retarget_release_image(canasta_default_image)" in (
        _target_expression())
    when = [str(c) for c in _compose_task()["when"]]
    assert "_upgrade_compose_target != ''" in when
    assert "_upgrade_compose_target != _mig.env.CANASTA_IMAGE" in when
    target = retarget_release_image(value, DEFAULT)
    return target != "" and target != value


class TestWhatGetsRewritten:
    def test_an_older_cli_pin_is_bumped(self):
        assert _would_rewrite("ghcr.io/canastawiki/canasta:3.5.19")

    def test_the_current_pin_is_left_alone(self):
        assert not _would_rewrite(DEFAULT)

    def test_an_older_canasta_base_pin_is_bumped_within_its_repository(self):
        old = "ghcr.io/canastawiki/canasta-base:3.6.0"
        assert _would_rewrite(old)
        assert retarget_release_image(old, "ghcr.io/canastawiki/canasta:3.6.1") \
            == "ghcr.io/canastawiki/canasta-base:3.6.1"


class TestWhatIsLeftAlone:
    def test_latest_survives(self):
        assert not _would_rewrite("ghcr.io/canastawiki/canasta:latest"), (
            "an instance deliberately tracking :latest is moved back to a "
            "fixed version on every upgrade, and never converges"
        )

    def test_a_named_tag_survives(self):
        assert not _would_rewrite("ghcr.io/canastawiki/canasta:edge")

    def test_a_dated_build_tag_survives(self):
        assert not _would_rewrite(
            "ghcr.io/canastawiki/canasta:1.43.9-20260801-228")

    def test_another_repository_survives(self):
        assert not _would_rewrite("ghcr.io/example/canasta:3.5.19")

    def test_a_local_build_survives(self):
        assert not _would_rewrite("canasta:local")

    def test_a_canastabase_release_line_tag_survives(self):
        assert not _would_rewrite("ghcr.io/canastawiki/canasta-base:1.3.24")

    def test_canasta_base_latest_survives(self):
        assert not _would_rewrite("ghcr.io/canastawiki/canasta-base:latest")


class TestTheChangeIsReported:
    def test_a_message_names_both_values(self):
        block = _compose_task()["block"]
        reports = [t for t in block if "ansible.builtin.debug" in t]
        assert reports, "the pin is rewritten with no output saying so"
        msg = reports[0]["ansible.builtin.debug"]["msg"]
        assert "_mig.env.CANASTA_IMAGE" in msg
        assert "_upgrade_compose_target" in msg
