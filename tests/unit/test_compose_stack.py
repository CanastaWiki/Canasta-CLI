"""Structural guards for the bundled docker-compose.yml."""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE_PATH = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml"
)


def _load_compose():
    with open(COMPOSE_PATH) as f:
        return yaml.safe_load(f)


class TestComposeUserRoot:
    def test_web_service_runs_as_root(self):
        services = _load_compose()["services"]
        assert services["web"].get("user") == "root", (
            "web service must declare `user: root` so rootless Podman "
            "doesn't run the canasta entrypoint as a non-root user"
        )

    def test_varnish_service_runs_as_root(self):
        services = _load_compose()["services"]
        assert services["varnish"].get("user") == "root", (
            "varnish service must declare `user: root` so rootless "
            "Podman doesn't lose read access to the bind-mounted VCL"
        )
