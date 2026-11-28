"""Tests for Caddy's trusted proxies (real client IP behind the edge).

Caddy's peer is always the HAProxy edge, which applies TRUSTED_PROXIES and
appends the recovered client address to X-Forwarded-For. Caddy trusts only
the edge's addresses (the private ranges, or CADDY_TRUSTED_PROXY_CIDRS) in
strict mode and forwards the resolved client IP to the backend.

Rendering tests evaluate the real Jinja template so a wrong header or
misplaced strict flag is caught.
"""

import os
import re

import jinja2
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CADDYFILE_J2 = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2",
)
REWRITE_CADDY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
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
    env.filters["split"] = lambda s, sep: str(s).split(sep)
    return env


def _render(**ctx):
    base = dict(
        _site_address="example.com",
        _backend="web:80",
        _observable=False,
        _os_user="",
        _os_password_hash="",
        _trusted_proxies_cidrs=["10.0.0.0/8", "192.0.2.0/24"],
    )
    base.update(ctx)
    src = _read(CADDYFILE_J2)
    return _ansible_jinja_env().from_string(src).render(**base)


def _trusted_cidrs(variables):
    """Evaluate rewrite_caddy.yml's real _trusted_proxies_cidrs expression."""
    tasks = yaml.safe_load(_read(REWRITE_CADDY))
    expr = next(
        t["ansible.builtin.set_fact"]["_trusted_proxies_cidrs"]
        for t in tasks
        if "_trusted_proxies_cidrs" in (t.get("ansible.builtin.set_fact") or {})
    )
    env = _ansible_jinja_env()
    env.undefined = jinja2.ChainableUndefined
    return env.from_string(
        "{{ (" + expr.strip()[2:-2] + ") | join(' ') }}"
    ).render(_env_caddy={"variables": variables})


class TestTrustedProxiesConfigKey:
    def test_is_known_key(self):
        defaults = yaml.safe_load(
            _read(os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml"))
        )
        names = [e["name"] for e in defaults["canasta_known_keys"]]
        assert "TRUSTED_PROXIES" in names

    def test_config_set_validates_value(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "_validate_trusted_proxies.yml",
        ))
        assert "TRUSTED_PROXIES" in content
        assert "'cloudflare', 'imperva'" in content


class TestTrustedProxiesCidrs:
    def test_private_ranges_by_default(self):
        assert _trusted_cidrs({}) == (
            "10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7")

    def test_override_replaces_the_default(self):
        assert _trusted_cidrs(
            {"CADDY_TRUSTED_PROXY_CIDRS": " 10.1.0.0/16, ,192.0.2.4 "}
        ) == "10.1.0.0/16 192.0.2.4"


class TestTrustedProxiesRendering:
    def test_static_cidrs_use_xff_and_strict(self):
        out = _render()
        assert "trusted_proxies static 10.0.0.0/8 192.0.2.0/24" in out
        assert "client_ip_headers X-Forwarded-For" in out
        assert "trusted_proxies_strict" in out

    def test_proxy_block_is_inside_global_options(self):
        out = _render()
        # Caddyfile.global is inlined and melded, not imported; the global
        # block (with `servers {`) must precede the site block.
        # Caddyfile.site stays a live import.
        assert "import /etc/caddy/Caddyfile.global" not in out
        assert "\n{\n    servers {" in out
        assert out.index("servers {") < out.index(
            "import /etc/caddy/Caddyfile.site"
        )


class TestBackendClientIpForwarding:
    """The backend must receive the resolved client IP, not the edge.

    reverse_proxy appends its immediate peer to X-Forwarded-For, and
    trusted_proxies only changes who Caddy considers the client for its own
    logging. Without header_up, MediaWiki trusts the private hops, reaches the
    appended edge address, and records that as the visitor.
    """

    HEADER_UP = "header_up X-Forwarded-For {client_ip}"

    def test_header_up_is_inside_the_backend_proxy_block(self):
        # Ordered after reverse_proxy's automatic X-Forwarded-For handling only
        # when nested in the proxy block; a bare directive is overwritten.
        out = _render()
        block = re.search(
            r"(?m)^\s*reverse_proxy web:80 \{\n(.*?)^\s*\}", out, re.S,
        )
        assert block, "backend proxy did not render as a block"
        assert self.HEADER_UP in block.group(1)

    def test_observability_branch_also_forwards(self):
        out = _render(
            _observable=True, _os_user="admin", _os_password_hash="hash",
        )
        block = re.search(
            r"(?m)^\s*reverse_proxy web:80 \{\n(.*?)^\s*\}", out, re.S,
        )
        assert block and self.HEADER_UP in block.group(1)
        # The dashboards proxy is a separate hop and is left alone.
        assert "reverse_proxy opensearch-dashboards:5601\n" in out


class TestCaddyPluginImage:
    def test_dockerfile_bundles_the_bouncer(self):
        content = _read(os.path.join(REPO_ROOT, "images", "caddy", "Dockerfile"))
        assert "caddy-crowdsec-bouncer/http" in content

    def test_publish_workflow_targets_canasta_caddy(self):
        content = _read(os.path.join(
            REPO_ROOT, ".github", "workflows", "docker-caddy.yml",
        ))
        assert "ghcr.io/canastawiki/canasta-caddy" in content
        assert "context: images/caddy" in content

    def test_no_vendored_ip_lists_remain(self):
        # Provider ranges reach the edge at render time, not from files
        # the repo maintains.
        assert not os.path.exists(
            os.path.join(REPO_ROOT, "roles", "orchestrator", "vars", "cloudflare_ips.yml")
        )
        assert not os.path.exists(
            os.path.join(REPO_ROOT, "roles", "orchestrator", "vars", "imperva_ips.yml")
        )
        assert not os.path.exists(
            os.path.join(REPO_ROOT, "scripts", "update_proxy_ips.py")
        )
