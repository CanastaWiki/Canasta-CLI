"""Tests for CADDY_TRUSTED_PROXIES (real client IP behind a CDN/WAF).

Covers the Compose Caddyfile rendering for the first-class `cloudflare`
and `imperva` modes and the generic comma-separated CIDR escape hatch,
the config-set validation guard, and the vendored provider ranges.

Rendering tests evaluate the real Jinja template so a wrong header,
missing range, or misplaced strict flag is caught.
"""

import os
import re

import jinja2
import pytest
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CADDYFILE_J2 = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2",
)
VARS_DIR = os.path.join(REPO_ROOT, "roles", "orchestrator", "vars")


def _read(path):
    with open(path) as f:
        return f.read()


def _provider_ranges(provider):
    data = yaml.safe_load(_read(os.path.join(VARS_DIR, "%s_ips.yml" % provider)))
    return data["%s_ip_ranges" % provider]


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


def _render_proxy(cidrs, header, strict):
    return _render(
        _trusted_proxies_enabled=True,
        _trusted_proxies_cidrs=cidrs,
        _trusted_proxies_headers=header,
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
        # Both provider keywords are accepted by the validator.
        assert "'cloudflare', 'imperva'" in content

    def test_rewrite_caddy_reads_key_and_loads_provider_vars(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
        ))
        assert "CADDY_TRUSTED_PROXIES" in content
        assert "{{ _tp_mode }}_ips.yml" in content


class TestTrustedProxiesRendering:
    def test_disabled_emits_no_servers_block(self):
        out = _render(_trusted_proxies_enabled=False)
        assert "servers {" not in out
        assert "trusted_proxies" not in out
        # Nothing else active -> no global options block at all.
        assert not re.search(r"(?m)^\{\s*$", out)

    def test_cloudflare_locks_ranges_and_header_no_strict(self):
        out = _render_proxy(_provider_ranges("cloudflare"), "Cf-Connecting-Ip", False)
        assert "servers {" in out
        assert "trusted_proxies static" in out
        assert "173.245.48.0/20" in out          # a real Cloudflare range
        assert "client_ip_headers Cf-Connecting-Ip" in out
        # Dedicated single-value header -> strict is moot and omitted.
        assert "trusted_proxies_strict" not in out

    def test_imperva_locks_ranges_and_header_no_strict(self):
        out = _render_proxy(_provider_ranges("imperva"), "Incap-Client-IP", False)
        assert "trusted_proxies static" in out
        assert "199.83.128.0/21" in out          # a real Imperva range
        assert "client_ip_headers Incap-Client-IP" in out
        assert "trusted_proxies_strict" not in out

    def test_explicit_cidrs_use_xff_and_strict(self):
        out = _render_proxy(["10.0.0.0/8", "192.0.2.0/24"], "X-Forwarded-For", True)
        assert "trusted_proxies static 10.0.0.0/8 192.0.2.0/24" in out
        assert "client_ip_headers X-Forwarded-For" in out
        assert "trusted_proxies_strict" in out

    def test_proxy_block_is_inside_global_options(self):
        out = _render_proxy(["10.0.0.0/8"], "X-Forwarded-For", True)
        # The servers block must sit before the imported global file /
        # site block (i.e. inside the global options braces).
        assert out.index("servers {") < out.index(
            "import /etc/caddy/Caddyfile.global"
        )


class TestVendoredRanges:
    @pytest.mark.parametrize("provider,sample", [
        ("cloudflare", "173.245.48.0/20"),
        ("imperva", "199.83.128.0/21"),
    ])
    def test_ranges_present_and_cidr_shaped(self, provider, sample):
        ranges = _provider_ranges(provider)
        assert sample in ranges
        assert len(ranges) > 1
        cidr = re.compile(r"^[0-9A-Fa-f:.]+(/[0-9]+)?$")
        for r in ranges:
            assert cidr.match(r), "%r is not CIDR-shaped" % r


class TestRefreshTooling:
    def test_generator_script_covers_both_providers(self):
        content = _read(os.path.join(REPO_ROOT, "scripts", "update_proxy_ips.py"))
        assert "cloudflare" in content
        assert "imperva" in content
        assert "my.imperva.com/api/integration/v1/ips" in content

    def test_refresh_workflow_opens_pr_not_commits_to_main(self):
        content = _read(os.path.join(
            REPO_ROOT, ".github", "workflows", "proxy-ips.yml",
        ))
        assert "update_proxy_ips.py" in content
        assert "gh pr create" in content
