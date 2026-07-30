"""Structural guards for the bundled docker-compose.yml."""

import os
import re

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


# NAME=${OTHER_NAME} entries that are deliberate, not typos. The database
# image expects MYSQL_ROOT_PASSWORD, while Canasta stores that password in
# .env as MYSQL_PASSWORD.
CROSS_NAMED_ENV = {("db", "MYSQL_ROOT_PASSWORD"): "MYSQL_PASSWORD"}

_SELF_INTERPOLATED = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)=\$\{([A-Za-z_][A-Za-z0-9_]*)([:\-?].*)?\}$"
)


class TestComposeEnvInterpolation:
    def test_env_entries_interpolate_from_their_own_key(self):
        """A NAME=${OTHER} entry makes `config set NAME` a silent no-op.

        The container gets OTHER's value however NAME is set, so the key
        looks settable — it is accepted, written to .env, and read back by
        `config get` — while never reaching the container.
        """
        offenders = []
        for service, body in _load_compose()["services"].items():
            for entry in body.get("environment") or []:
                if not isinstance(entry, str):
                    continue
                match = _SELF_INTERPOLATED.match(entry)
                if not match:
                    continue
                name, source = match.group(1), match.group(2)
                if name == source:
                    continue
                if CROSS_NAMED_ENV.get((service, name)) == source:
                    continue
                offenders.append("%s: %s" % (service, entry))

        assert not offenders, (
            "compose environment entries must interpolate from the .env key "
            "of the same name, or be listed in CROSS_NAMED_ENV as "
            "deliberate:\n  " + "\n  ".join(offenders)
        )
