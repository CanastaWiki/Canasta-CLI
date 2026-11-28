"""A pre-release Canasta image (5.0.0-rc.1) is a release pin like 5.0.0.

The CLI that installs an RC pins canasta:5.0.0-rc.1. Upgrade, restore,
doctor and gitops must treat that pin as the CLI's own, and versions must
order as semver orders them.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "roles", "common", "module_utils"))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

from canasta_image_pin import (
    base_image_for,
    is_cli_image_pin,
    predates_release_major,
    release_image,
    retarget_release_image,
    version_key,
)
from canasta_images import canasta_retarget_base_image

CANASTA = "ghcr.io/canastawiki/canasta:"
BASE = "ghcr.io/canastawiki/canasta-base:"
RC1 = CANASTA + "5.0.0-rc.1"


@pytest.mark.parametrize("older,newer", [
    ("5.0.0-rc.1", "5.0.0-rc.2"),
    ("5.0.0-rc.2", "5.0.0-rc.10"),
    ("5.0.0-beta.1", "5.0.0-rc.1"),
    ("5.0.0-rc.1", "5.0.0"),
    ("4.34.1", "5.0.0-rc.1"),
    ("5.0.0", "5.0.1-rc.1"),
    ("5.0.0-1", "5.0.0-rc"),
])
def test_versions_order_as_semver(older, newer):
    assert version_key(older) < version_key(newer)


@pytest.mark.parametrize("text", [
    "5.0", "5.0.0-", "5.0.0-rc..1", "v5.0.0", "5.0.0+build", "latest", "", None,
])
def test_other_text_is_not_a_version(text):
    assert version_key(text) is None


def test_an_rc_image_is_the_clis_own_pin():
    assert is_cli_image_pin(RC1)
    assert is_cli_image_pin('"%s"' % RC1)
    assert release_image(RC1) == (CANASTA.rstrip(":"), version_key("5.0.0-rc.1"))
    assert release_image(BASE + "5.0.0-rc.1") is not None


def test_upgrade_moves_a_4x_pin_to_the_rc():
    assert retarget_release_image(CANASTA + "3.6.2", RC1) == RC1


def test_upgrade_moves_an_rc_pin_to_the_release():
    assert retarget_release_image(RC1, CANASTA + "5.0.0") == CANASTA + "5.0.0"
    assert retarget_release_image(
        BASE + "5.0.0-rc.1", CANASTA + "5.0.0-rc.2") == BASE + "5.0.0-rc.2"


def test_a_4x_snapshot_predates_the_rc():
    assert predates_release_major(CANASTA + "3.6.2", RC1)
    assert not predates_release_major(RC1, CANASTA + "5.0.0")


def test_a_build_from_base_moves_forward_to_the_rc_only():
    assert canasta_retarget_base_image(
        ["BASE_IMAGE=" + CANASTA + "3.6.2"], RC1) == ["BASE_IMAGE=" + RC1]
    # The release is newer than its RC, so an instance on 5.0.0 stays there.
    assert canasta_retarget_base_image(
        ["BASE_IMAGE=" + CANASTA + "5.0.0"], RC1) == [
        "BASE_IMAGE=" + CANASTA + "5.0.0"]


def test_the_base_image_of_an_rc():
    assert base_image_for(RC1) == BASE + "5.0.0-rc.1"


def test_a_canastabase_tag_is_still_not_a_pin():
    assert release_image(BASE + "1.3.33") is None
