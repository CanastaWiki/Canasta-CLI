"""Tests for CADDY_TRUSTED_PROXIES (real client IP behind a CDN/WAF).

The `cloudflare` and `imperva` modes render a dynamic caddy-cdn-ranges
source, so Caddy keeps the provider's edge ranges current in-process —
no vendored list, no redeploy. A generic comma-separated CIDR list uses
Caddy's built-in static source on stock Caddy.

Rendering tests evaluate the real Jinja template so a wrong header,
wrong provider source, or misplaced strict flag is caught.
"""

import ast
import os
import re
import sys

import jinja2
import yaml

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "filter_plugins")
)
from canasta_caddy import caddy_explicit_http_hosts  # noqa: E402


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


def _render_proxy_with(**ctx):
    """Cloudflare mode plus whatever the caller overrides."""
    base = dict(
        _trusted_proxies_enabled=True,
        _tp_mode="cloudflare",
        _tp_dynamic=True,
        _trusted_proxies_headers="Cf-Connecting-Ip",
        _trusted_proxies_cidrs=[],
        _trusted_proxies_strict=False,
    )
    base.update(ctx)
    return _render(**base)


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
        assert "trusted_proxies cdn_ranges" in out
        assert "provider cloudflare" in out
        assert "interval 12h" in out
        # Dynamic, not a hard-coded static list.
        assert "trusted_proxies static" not in out
        assert "trusted_proxies_strict" not in out

    def test_imperva_uses_dynamic_cdn_ranges_with_open_api(self):
        out = _render_proxy("imperva", "Incap-Client-IP", dynamic=True)
        assert "client_ip_headers Incap-Client-IP" in out
        assert "trusted_proxies cdn_ranges" in out
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
        # Caddyfile.global is now inlined + melded (not imported); the global
        # block (with `servers {`) must precede the site block. Caddyfile.site
        # stays a live import.
        assert "import /etc/caddy/Caddyfile.global" not in out
        assert out.index("servers {") < out.index(
            "import /etc/caddy/Caddyfile.site"
        )


class TestBackendClientIpForwarding:
    """The backend must receive the resolved client IP, not the CDN edge.

    reverse_proxy appends its immediate peer to X-Forwarded-For, and
    trusted_proxies only changes who Caddy considers the client for its own
    logging. Without header_up, MediaWiki trusts the private hops, reaches the
    appended CDN address, and records that as the visitor.
    """

    HEADER_UP = "header_up X-Forwarded-For {client_ip}"

    def test_backend_proxy_forwards_resolved_client_ip(self):
        out = _render_proxy("cloudflare", "Cf-Connecting-Ip", dynamic=True)
        assert self.HEADER_UP in out

    def test_header_up_is_inside_the_backend_proxy_block(self):
        # Ordered after reverse_proxy's automatic X-Forwarded-For handling only
        # when nested in the proxy block; a bare directive is overwritten.
        out = _render_proxy("cloudflare", "Cf-Connecting-Ip", dynamic=True)
        block = re.search(
            r"(?m)^\s*reverse_proxy web:80 \{\n(.*?)^\s*\}", out, re.S,
        )
        assert block, "backend proxy did not render as a block"
        assert self.HEADER_UP in block.group(1)

    def test_static_cidr_mode_also_forwards(self):
        out = _render_proxy(
            "10.0.0.0/8", "X-Forwarded-For", dynamic=False,
            cidrs=["10.0.0.0/8"], strict=True,
        )
        assert self.HEADER_UP in out

    def test_observability_branch_also_forwards(self):
        out = _render_proxy_with(
            _observable=True, _os_user="admin", _os_password_hash="hash",
        )
        assert self.HEADER_UP in out
        # The dashboards proxy is a separate hop and is left alone.
        assert "reverse_proxy opensearch-dashboards:5601" in out

    def test_absent_without_trusted_proxies(self):
        # No CDN in front means the peer genuinely is the client, and Caddy's
        # own X-Forwarded-For handling is already correct.
        out = _render(_trusted_proxies_enabled=False)
        assert "header_up" not in out
        assert "reverse_proxy web:80" in out


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


class TestPortEightyRedirectServer:
    def test_redirect_server_is_declared_for_each_name(self):
        out = _render_proxy_with(
            _redirect_server_names=["a.example.com", "b.example.com"])
        assert "http://a.example.com, http://b.example.com {" in out
        assert "redir https://{host}{uri} 308" in out
        # Both servers must log to the file CrowdSec reads.
        assert out.count("output file /var/log/caddy/access.log") == 2

    def test_redirect_server_enforces_crowdsec_when_active(self):
        out = _render_proxy_with(
            _redirect_server_names=["a.example.com"], _crowdsec_active=True)
        redirect_block = out[out.index("http://a.example.com {"):]
        assert "crowdsec" in redirect_block.split("}")[0]

    def test_no_redirect_server_when_no_names(self):
        out = _render_proxy_with(_redirect_server_names=[])
        assert "http://" not in out
        assert "redir " not in out

    def test_absent_variable_renders_nothing(self):
        out = _render_proxy("cloudflare", "Cf-Connecting-Ip", dynamic=True)
        assert "redir " not in out


def _redirect_names(http_only, trusted, names, user_global=""):
    """Evaluate rewrite_caddy.yml's real _redirect_server_names expression."""
    tasks = yaml.safe_load(_read(os.path.join(
        REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
    )))
    expr = next(
        t["ansible.builtin.set_fact"]["_redirect_server_names"]
        for t in tasks
        if "_redirect_server_names" in (t.get("ansible.builtin.set_fact") or {})
    )
    env = _ansible_jinja_env()
    env.filters["caddy_explicit_http_hosts"] = caddy_explicit_http_hosts
    out = env.from_string(expr).render(
        _http_only=http_only,
        _trusted_proxies_enabled=trusted,
        _server_names=names,
        _caddyfile_global_content=user_global,
    )
    return ast.literal_eval(out.strip())


class TestRedirectServerNameSelection:
    def test_claims_every_wiki_server_name(self):
        assert _redirect_names(
            False, True, ["a.example.com", "b.example.com"],
        ) == ["a.example.com", "b.example.com"]

    def test_empty_without_trusted_proxies(self):
        # Caddy's built-in redirect already logs the true client IP when
        # nothing is in front of it.
        assert _redirect_names(False, False, ["a.example.com"]) == []

    def test_empty_in_plain_http_mode(self):
        # K8s / CADDY_AUTO_HTTPS=off: there is no redirect to take over.
        assert _redirect_names(True, True, ["a.example.com"]) == []

    def test_skips_names_the_user_already_serves_over_http(self):
        # Claiming one twice is "ambiguous site definition" — Caddy refuses to
        # load the config at all.
        assert _redirect_names(
            False, True, ["a.example.com", "b.example.com"],
            user_global="http://a.example.com {\n    respond ok\n}\n",
        ) == ["b.example.com"]
