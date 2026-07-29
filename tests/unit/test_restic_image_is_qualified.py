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

The K8s references matter for the same reason on a CRI-O cluster;
containerd assumes docker.io, so they happen to work under k3s today.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

SITES = (
    os.path.join("roles", "orchestrator", "tasks", "run_backup.yml"),
    os.path.join("direct_commands", "backup.py"),
    os.path.join("roles", "backup", "tasks", "restore_k8s.yml"),
    os.path.join("roles", "orchestrator", "tasks", "k8s_run_backup.yml"),
)

# `restic/restic` not preceded by a registry host.
UNQUALIFIED = re.compile(r"(?<![\w./-])restic/restic")


def _read(rel):
    with open(os.path.join(REPO_ROOT, rel)) as f:
        return f.read()


class TestEveryResticReferenceIsQualified:
    def test_no_bare_short_name(self):
        for rel in SITES:
            body = _read(rel)
            assert not UNQUALIFIED.search(body), (
                "%s uses the short name; podman refuses it with no "
                "unqualified-search registry configured" % rel
            )

    def test_the_image_is_still_referenced(self):
        # Guard against the regex passing because the reference was
        # deleted rather than qualified.
        for rel in SITES:
            assert "restic/restic" in _read(rel)

    def test_it_is_qualified_to_docker_io(self):
        for rel in SITES:
            assert "docker.io/restic/restic" in _read(rel)


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
