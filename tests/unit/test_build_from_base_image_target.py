"""A --build-from instance is rebuilt on the upgrade's Canasta release.

  - a BASE_IMAGE build arg pinned to an older Canasta release is moved to
    the target release, in the rebuild and in the registry;
  - any other BASE_IMAGE is kept as given;
  - an overlay source with no BASE_IMAGE builds on the target Canasta image,
    while Canasta source keeps building on CanastaBase.
"""

import os
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

from canasta_images import (  # noqa: E402
    FilterModule,
    canasta_release_version,
    canasta_retarget_base_image,
)

BUILD_FROM = os.path.join(
    REPO_ROOT, "roles", "imagebuild", "tasks", "build_from_source.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")

TARGET = "ghcr.io/canastawiki/canasta:3.5.29"


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _task(tasks, name):
    return next(t for t in tasks if t.get("name") == name)


class TestCanastaReleaseVersion:
    def test_parses_release(self):
        assert canasta_release_version(TARGET) == (3, 5, 29)

    def test_rejects_non_releases(self):
        for ref in (
            "ghcr.io/canastawiki/canasta:latest",
            "ghcr.io/canastawiki/canasta-base:1.3.24",
            "ghcr.io/canastawiki/canasta@sha256:abcd",
            "ghcr.io/canastawiki/canasta:3.5.29-rc1",
            "example.com/canasta:3.5.28",
            "canasta:local",
            "",
            None,
        ):
            assert canasta_release_version(ref) is None, ref


class TestRetargetBaseImage:
    def test_moves_older_release(self):
        args = ["MW_VERSION=master",
                "BASE_IMAGE=ghcr.io/canastawiki/canasta:3.5.28"]
        assert canasta_retarget_base_image(args, TARGET) == [
            "MW_VERSION=master", "BASE_IMAGE=" + TARGET]

    def test_compares_numerically(self):
        args = ["BASE_IMAGE=ghcr.io/canastawiki/canasta:3.5.9"]
        assert canasta_retarget_base_image(
            args, "ghcr.io/canastawiki/canasta:3.5.10") == [
            "BASE_IMAGE=ghcr.io/canastawiki/canasta:3.5.10"]

    def test_keeps_same_or_newer_release(self):
        for pin in ("3.5.29", "3.5.30", "3.6.0", "4.0.0"):
            args = ["BASE_IMAGE=ghcr.io/canastawiki/canasta:" + pin]
            assert canasta_retarget_base_image(args, TARGET) == args

    def test_keeps_other_base_images(self):
        for ref in (
            "ghcr.io/canastawiki/canasta:latest",
            "ghcr.io/canastawiki/canasta-base:1.3.24",
            "ghcr.io/canastawiki/canasta@sha256:abcd",
            "example.com/canasta:3.5.28",
            "canasta:merged",
        ):
            args = ["BASE_IMAGE=" + ref]
            assert canasta_retarget_base_image(args, TARGET) == args

    def test_keeps_args_when_target_is_not_a_release(self):
        args = ["BASE_IMAGE=ghcr.io/canastawiki/canasta:3.5.28"]
        assert canasta_retarget_base_image(
            args, "ghcr.io/canastawiki/canasta:latest") == args

    def test_only_touches_base_image_key(self):
        args = ["OTHER_IMAGE=ghcr.io/canastawiki/canasta:3.5.28"]
        assert canasta_retarget_base_image(args, TARGET) == args

    def test_empty(self):
        assert canasta_retarget_base_image(None, TARGET) == []
        assert canasta_retarget_base_image([], TARGET) == []

    def test_registered_as_filter(self):
        assert "canasta_retarget_base_image" in FilterModule().filters()


class TestUpgradeWiring:
    def test_rebuild_uses_retargeted_args(self):
        tasks = _tasks(UPGRADE_MAIN)
        fact = _task(
            tasks,
            "Move a BASE_IMAGE pinned to an older Canasta release to the target")
        expr = fact["ansible.builtin.set_fact"]["_upgrade_build_args"]
        assert "canasta_retarget_base_image(canasta_default_image)" in expr
        assert "when" not in fact
        rebuild = _task(tasks, "Rebuild image from source")
        assert rebuild["vars"]["build_arg"] == "{{ _upgrade_build_args }}"

    def test_registry_records_moved_base_after_the_rebuild(self):
        tasks = _tasks(UPGRADE_MAIN)
        names = [t.get("name", "") for t in tasks]
        record = _task(tasks, "Record the moved BASE_IMAGE in the registry")
        assert names.index("Rebuild image from source") < names.index(
            record["name"])
        assert "_upgrade_rebuild | bool" in record["when"]
        update = record["block"][0]
        reg = update["canasta_registry"]
        assert reg["state"] == "update"
        assert reg["build_args"] == "{{ _upgrade_build_args }}"
        assert update["delegate_to"] == "canasta_controller"
        assert update["vars"]["ansible_connection"] == "local"
        report = record["block"][1]
        msg = report["ansible.builtin.debug"]["msg"]
        assert "instance_id" in msg and "canasta_default_image" in msg


class TestOverlayDefaultBase:
    def test_overlay_defaults_to_target_canasta_image(self):
        tasks = _tasks(BUILD_FROM)
        names = [t.get("name", "") for t in tasks]
        probe = _task(
            tasks, "Check whether the Canasta source is the Canasta repository")
        assert probe["ansible.builtin.stat"]["path"] == (
            "{{ build_from }}/Canasta/contents.yaml")
        published = _task(tasks, "Use published base image")
        assert names.index(probe["name"]) < names.index(published["name"])
        expr = published["ansible.builtin.set_fact"]["_base_image_arg"]
        assert "'ghcr.io/canastawiki/canasta-base:latest'" in expr
        assert "if _canasta_contents.stat.exists" in expr
        assert "else canasta_default_image" in expr
        assert published["when"] == "not _base_dockerfile.stat.exists"

    def test_local_canastabase_build_unchanged(self):
        tasks = _tasks(BUILD_FROM)
        block = _task(tasks, "Build CanastaBase locally")
        assert block["when"] == "_base_dockerfile.stat.exists"
        local = _task(block["block"], "Set base image to local build")
        assert local["ansible.builtin.set_fact"]["_base_image_arg"] == (
            "canasta-base:local")
