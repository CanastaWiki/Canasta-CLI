"""Tests for CADDY_TRUSTED_PROXIES (real client IP behind a CDN/WAF).

The `cloudflare` and `imperva` modes render a dynamic caddy-cdn-ranges
source, so Caddy keeps the provider's edge ranges current in-process —
no vendored list, no redeploy. A generic comma-separated CIDR list uses
Caddy's built-in static source on stock Caddy.

Rendering tests evaluate the real Jinja template so a wrong header,
wrong provider source, or misplaced strict flag is caught.
"""

import os
import re

import jinja2
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CADDYFILE_J2 = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2",
)


def _read(path):
    with open(path) as f:
        return f.read()


def _ansible_jinja_env():
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["ternary"] = lambda cond, a, b: a if cond else b
    env.filters["regex_replace"] = lambda s, pat, repl="": re.sub(pat, repl, str(s))
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes", "y")
    return env


def _render(**ctx):
    base = dict(
        _site_address="example.com",
        _backend="web:80",
        _observable=False,
        _os_user="",
        _os_password_hash="",
        _staging_certs=False,
    )
    base.update(ctx)
    src = _read(CADDYFILE_J2)
    return _ansible_jinja_env().from_string(src).render(**base)


def _render_proxy(mode, header, dynamic, cidrs=None, strict=False):
    return _render(
        _trusted_proxies_enabled=True,
        _tp_mode=mode,
        _tp_dynamic=dynamic,
        _trusted_proxies_headers=header,
        _trusted_proxies_cidrs=cidrs or [],
        _trusted_proxies_strict=strict,
    )


class TestTrustedProxiesConfigKey:
    def test_is_known_key(self):
        defaults = yaml.safe_load(
            _read(os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml"))
        )
        names = [e["name"] for e in defaults["canasta_known_keys"]]
        assert "CADDY_TRUSTED_PROXIES" in names

    def test_side_effects_validates_value(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml",
        ))
        assert "CADDY_TRUSTED_PROXIES" in content
        assert "'cloudflare', 'imperva'" in content

    def test_rewrite_caddy_drives_dynamic_source(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
        ))
        assert "CADDY_TRUSTED_PROXIES" in content
        assert "_tp_dynamic" in content
        # The repo must not reload a vendored IP list anymore.
        assert "_ips.yml" not in content


class TestTrustedProxiesRendering:
    def test_disabled_emits_no_servers_block(self):
        out = _render(_trusted_proxies_enabled=False)
        assert "servers {" not in out
        assert "trusted_proxies" not in out
        assert not re.search(r"(?m)^\{\s*$", out)

    def test_cloudflare_uses_dynamic_cdn_ranges(self):
        out = _render_proxy("cloudflare", "Cf-Connecting-Ip", dynamic=True)
        assert "client_ip_headers Cf-Connecting-Ip" in out
        assert "source cdn_ranges" in out
        assert "provider cloudflare" in out
        assert "interval 12h" in out
        # Dynamic, not a hard-coded static list.
        assert "trusted_proxies static" not in out
        assert "trusted_proxies_strict" not in out

    def test_imperva_uses_dynamic_cdn_ranges_with_open_api(self):
        out = _render_proxy("imperva", "Incap-Client-IP", dynamic=True)
        assert "client_ip_headers Incap-Client-IP" in out
        assert "source cdn_ranges" in out
        assert "my.imperva.com/api/integration/v1/ips" in out
        # JMESPath extraction of the two arrays Imperva's API returns.
        assert '"ipRanges"' in out
        assert '"ipv6Ranges"' in out
        assert "trusted_proxies static" not in out

    def test_explicit_cidrs_use_static_xff_and_strict(self):
        out = _render_proxy(
            "10.0.0.0/8,192.0.2.0/24", "X-Forwarded-For", dynamic=False,
            cidrs=["10.0.0.0/8", "192.0.2.0/24"], strict=True,
        )
        assert "trusted_proxies static 10.0.0.0/8 192.0.2.0/24" in out
        assert "client_ip_headers X-Forwarded-For" in out
        assert "trusted_proxies_strict" in out
        assert "cdn_ranges" not in out

    def test_proxy_block_is_inside_global_options(self):
        out = _render_proxy("cloudflare", "Cf-Connecting-Ip", dynamic=True)
        assert out.index("servers {") < out.index(
            "import /etc/caddy/Caddyfile.global"
        )


class TestCaddyPluginImage:
    def test_dockerfile_bundles_both_plugins(self):
        content = _read(os.path.join(REPO_ROOT, "images", "caddy", "Dockerfile"))
        assert "caddy-crowdsec-bouncer/http" in content
        assert "sarumaj/caddy-cdn-ranges" in content

    def test_publish_workflow_targets_canasta_caddy(self):
        content = _read(os.path.join(
            REPO_ROOT, ".github", "workflows", "docker-caddy.yml",
        ))
        assert "ghcr.io/canastawiki/canasta-caddy" in content
        assert "context: images/caddy" in content

    def test_no_vendored_ip_lists_remain(self):
        # The repo must not maintain provider IP lists — instances refresh
        # them at runtime via caddy-cdn-ranges.
        assert not os.path.exists(
            os.path.join(REPO_ROOT, "roles", "orchestrator", "vars", "cloudflare_ips.yml")
        )
        assert not os.path.exists(
            os.path.join(REPO_ROOT, "roles", "orchestrator", "vars", "imperva_ips.yml")
        )
        assert not os.path.exists(
            os.path.join(REPO_ROOT, "scripts", "update_proxy_ips.py")
        )
