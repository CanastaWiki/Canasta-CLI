"""Container images must carry their registry.

Podman does not assume docker.io for a short name. With no
`unqualified-search-registries` configured — the state of a stock
install — `restic/restic` is refused outright:

    Error: short-name "restic/restic" did not resolve to an alias and no
    unqualified-search registries are defined in
    "/etc/containers/registries.conf"

so `canasta backup` could not run at all on Podman.

The Canasta stack itself was never affected because every image in the
rendered compose file is already fully qualified (docker.io/library/...,
ghcr.io/..., docker.elastic.co/...). restic was the one that was missed.
"""

import os
import re

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

SITES = (
    os.path.join("roles", "orchestrator", "tasks", "run_backup.yml"),
    os.path.join("direct_commands", "backup.py"),
)

# `restic/restic` not preceded by a registry host.
UNQUALIFIED = re.compile(r"(?<![\w./-])restic/restic")


def _read(rel):
    with open(os.path.join(REPO_ROOT, rel)) as f:
        return f.read()


TOOL_VERSIONS = os.path.join("vars", "tool_versions.yml")


def _check_no_bare_short_name(rel):
    body = _read(rel)
    assert not UNQUALIFIED.search(body), (
        "%s uses the short name; podman refuses it with no "
        "unqualified-search registry configured" % rel
    )


def _check_site_uses_the_pinned_image(rel):
    # Guard against the regex passing because the reference was
    # deleted rather than qualified.
    assert "restic_image" in _read(rel), rel


class TestEveryResticReferenceIsQualified:
    @pytest.mark.parametrize("rel", SITES + (TOOL_VERSIONS,))
    def test_no_bare_short_name(self, rel):
        _check_no_bare_short_name(rel)

    @pytest.mark.parametrize("rel", SITES)
    def test_every_site_uses_the_pinned_image(self, rel):
        _check_site_uses_the_pinned_image(rel)

    def test_the_pin_is_qualified_to_docker_io_and_versioned(self):
        image = yaml.safe_load(_read(TOOL_VERSIONS))["restic_image"]
        assert image.startswith("docker.io/restic/restic:")
        assert image.rsplit(":", 1)[1] not in ("", "latest")


# The helper container is run in more places than restic, and it runs
# FIRST during a backup — the staging step. Qualifying restic alone would
# still have left `canasta backup` broken on Podman.
ALPINE_SITES = (
    os.path.join("roles", "orchestrator", "tasks", "run_backup.yml"),
    os.path.join("roles", "orchestrator", "tasks", "restore_instance.yml"),
    os.path.join("roles", "upgrade", "tasks", "migrations",
                 "mysql_to_mariadb.yml"),
)

# `alpine` used as an image argument, not inside a tag like
# caddy:2.10.2-alpine or in prose.
BARE_ALPINE = re.compile(r"(?<![\w./:-])alpine(?=\s+(sh|test)\b|\s*$)",
                         re.MULTILINE)


class TestTheHelperImageIsQualifiedToo:
    def test_no_bare_alpine_invocation(self):
        for rel in ALPINE_SITES:
            body = _read(rel)
            hits = [m.group(0) for m in BARE_ALPINE.finditer(body)]
            assert not hits, (
                "%s runs the short name `alpine`; podman refuses it the "
                "same way it refuses restic/restic" % rel
            )

    def test_the_helper_is_still_used(self):
        for rel in ALPINE_SITES:
            assert "alpine" in _read(rel)

    def test_it_is_qualified_to_the_library_namespace(self):
        for rel in ALPINE_SITES:
            assert "docker.io/library/alpine" in _read(rel)
