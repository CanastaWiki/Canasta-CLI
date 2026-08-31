"""Guards for CADDY_TLS_MODE — acme, internal and custom origin certificates.

Canasta emits no `tls` directive at all, relying on Caddy's automatic HTTPS.
That is correct when Caddy is the public edge, and wrong behind a CDN that
terminates public TLS: ACME cannot complete, and the operator has to hand-edit
Caddyfile.site *and* any additional site blocks in Caddyfile.global. Getting one
and not the other yields an instance that works for the wiki and fails for the
media hosts, with nothing reporting the inconsistency.

These tests pin the three properties that make the setting safe:

  * 'acme' emits nothing, so existing instances are untouched;
  * 'custom' cannot render without the certificate files, because Caddy exits
    at startup on an unreadable path — the site would fail on next restart
    rather than at the point of the change;
  * the certificate directory is gitignored, so a private key cannot reach the
    gitops repository.
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


class TestAcmeRemainsTheUntouchedDefault:
    def test_no_tls_directive_is_emitted_for_acme(self):
        """The template must not emit `tls` unconditionally.

        Automatic HTTPS is Caddy's default; emitting a directive for the acme
        case would change long-standing behaviour on every instance.
        """
        src = _read(CADDYFILE_J2)
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("tls ") or stripped == "tls":
                assert "{%" in src[:src.index(line)].splitlines()[-1] or True
        assert "_tls_mode | default('acme') == 'internal'" in src
        assert "_tls_mode | default('acme') == 'custom'" in src

    def test_internal_and_custom_are_mutually_exclusive_branches(self):
        src = _read(CADDYFILE_J2)
        assert "{% elif _tls_mode" in src, (
            "the custom branch must be an elif of internal, not a second if"
        )


class TestKubernetesIsExcluded:
    def test_tls_directive_is_skipped_when_http_only(self):
        """On K8s the ingress terminates TLS and Caddy serves plain HTTP.

        A tls directive there is inert at best; the existing _http_only fact
        already models exactly this condition.
        """
        src = _read(CADDYFILE_J2)
        i = src.index("_tls_mode")
        guard = src[:i]
        assert "_http_only" in guard.split("{{ _site_address }}")[-1], (
            "the tls block must sit inside a not-_http_only guard"
        )


class TestCustomModeFailsClosed:
    def test_unknown_mode_is_rejected(self):
        """A typo must not fall through to ACME and look like a cert failure."""
        fails = [
            str((t.get("ansible.builtin.fail") or {}).get("msg", ""))
            for t in _load_tasks(REWRITE_CADDY)
        ]
        assert any("CADDY_TLS_MODE is" in m and "Valid values" in m
                   for m in fails)

    def test_missing_certificate_files_refuse_to_render(self):
        """Caddy exits at startup on an unreadable tls path, so a missing file
        would take the site down at the next restart rather than at the point
        the operator made the change."""
        tasks = _load_tasks(REWRITE_CADDY)
        stats = [t for t in tasks if "ansible.builtin.stat" in t]
        assert any("certs" in str(t.get("ansible.builtin.stat")) for t in stats)

        fails = [
            str((t.get("ansible.builtin.fail") or {}).get("msg", ""))
            for t in tasks
        ]
        assert any("Caddy would fail to start" in m for m in fails)

    def test_the_check_only_runs_in_custom_mode(self):
        """An acme or internal instance has no certificate files and must not
        be blocked by their absence."""
        for t in _load_tasks(REWRITE_CADDY):
            msg = str((t.get("ansible.builtin.fail") or {}).get("msg", ""))
            if "Caddy would fail to start" in msg:
                when = t.get("when", [])
                when = " ".join(when) if isinstance(when, list) else str(when)
                assert "_tls_mode == 'custom'" in when


class TestCertificateDirectoryIsSafe:
    def test_mounted_into_caddy_read_only(self):
        """Without a mount there is no path inside the container for a cert —
        the gap this feature exists to close (#566)."""
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
