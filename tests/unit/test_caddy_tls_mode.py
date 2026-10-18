"""Guards for CADDY_TLS_MODE — acme, internal and custom origin certificates.

The HAProxy edge obtains and serves every instance's certificates; Caddy is a
plain internal HTTP server. These tests pin what stays with the instance: the
settings are declared, and the certificate directory a custom certificate
lives in is gitignored, so a private key cannot reach the gitops repository.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CADDYFILE_J2 = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2")
REWRITE_CADDY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml")
GITIGNORE_DEFAULT = os.path.join(
    REPO_ROOT, "roles", "gitops", "files", "gitignore.default")
CONFIG_DEFAULTS = os.path.join(
    REPO_ROOT, "roles", "config", "defaults", "main.yml")
CREATE_MAIN = os.path.join(REPO_ROOT, "roles", "create", "tasks", "main.yml")


def _read(path):
    with open(path) as f:
        return f.read()


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _load_tasks(path):
    with open(path) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


class TestSettingsAreDeclared:
    """meta/command_definitions.yml drives `canasta config`, so an undeclared
    key cannot be set without --force and is absent from `config get`."""

    def _names(self):
        with open(CONFIG_DEFAULTS) as f:
            doc = yaml.safe_load(f)
        out = []

        def walk(node):
            if isinstance(node, dict):
                if "name" in node and "group" in node:
                    out.append(node["name"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(doc)
        return out

    def test_all_three_settings_declared(self):
        names = self._names()
        for key in ("CADDY_TLS_MODE", "CADDY_TLS_CERT", "CADDY_TLS_KEY"):
            assert key in names, f"{key} is not declared in config defaults"

    def test_mode_defaults_to_acme(self):
        """Anything else would change behaviour for every existing instance."""
        src = _read(CONFIG_DEFAULTS)
        block = src[src.index("CADDY_TLS_MODE"):]
        assert re.search(r'default:\s*"acme"', block[:600])


class TestCertificateDirectoryIsSafe:
    def test_mounted_into_caddy_read_only(self):
        """Without a mount there is no path inside the container for a cert —
        the gap this feature exists to close."""
        src = _read(COMPOSE)
        assert "./config/certs:/etc/caddy/certs:ro" in src

    def test_gitignored_so_a_private_key_cannot_be_committed(self):
        rules = [
            ln.strip() for ln in _read(GITIGNORE_DEFAULT).splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert "config/certs/" in rules

    def test_created_by_the_create_role(self):
        """A bind mount to a missing path is created by Docker as root, which
        leaves the operator unable to place a certificate without sudo."""
        src = _read(CREATE_MAIN)
        assert "config/certs" in src
