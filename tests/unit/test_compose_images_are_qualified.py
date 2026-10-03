"""Every image in the shipped compose file must name its registry.

Podman does not assume docker.io for a short name; without an
`unqualified-search-registries` entry it refuses to pull, so one bare
reference keeps that service (or the whole stack) from starting.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

COMPOSE_FILES = (
    os.path.join("roles", "orchestrator", "files", "compose",
                 "docker-compose.yml"),
)

# ${VAR:-default} or ${VAR-default}
VAR_WITH_DEFAULT = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:?-(.*)\}$")


def _effective_image(ref):
    m = VAR_WITH_DEFAULT.match(ref)
    return m.group(1) if m else ref


def _has_registry(ref):
    if "/" not in ref:
        return False
    host = ref.split("/", 1)[0]
    return "." in host or ":" in host or host == "localhost"


def _images(rel):
    with open(os.path.join(REPO_ROOT, rel)) as f:
        services = yaml.safe_load(f).get("services", {})
    return {name: svc["image"] for name, svc in services.items()
            if "image" in svc}


class TestHasRegistry:
    def test_qualified_forms(self):
        assert _has_registry("docker.io/library/mariadb:11.4")
        assert _has_registry("ghcr.io/canastawiki/canasta:latest")
        assert _has_registry("10.43.0.2:5000/x:local")
        assert _has_registry("localhost/x:local")

    def test_short_names(self):
        assert not _has_registry("mariadb:11.4")
        assert not _has_registry("crowdsecurity/crowdsec:v1.6.11")

    def test_default_is_extracted(self):
        assert _effective_image("${X:-docker.io/library/caddy:2}") == (
            "docker.io/library/caddy:2")
        assert _effective_image("${X-caddy:2}") == "caddy:2"


class TestShippedComposeImages:
    def test_compose_files_define_images(self):
        for rel in COMPOSE_FILES:
            assert _images(rel), "%s declares no service images" % rel

    def test_every_image_names_a_registry(self):
        bad = []
        for rel in COMPOSE_FILES:
            for name, ref in _images(rel).items():
                if not _has_registry(_effective_image(ref)):
                    bad.append("%s: %s -> %s" % (rel, name, ref))
        assert not bad, (
            "unqualified image references (podman refuses short names):\n"
            + "\n".join(bad)
        )
