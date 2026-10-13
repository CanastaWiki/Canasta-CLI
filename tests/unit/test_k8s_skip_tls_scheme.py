"""A Kubernetes instance created with --skip-tls is served over plain HTTP.

The values overlay create writes and the chart must agree: ingress.tls
decides both whether the Ingress terminates TLS and the scheme of the
MW_SITE_SERVER the web pod receives. Without it, a --skip-tls instance got
an https:// $wgServer (robots.txt and sitemaps advertised https) and an
Ingress tls block pointing at a certificate Secret that never exists.
"""

import os
import re
import shutil
import subprocess

import jinja2
import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHART = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta")
VALUES_TEMPLATE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "k8s_values.yaml.j2")


def _render_overlay(**ctx):
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
    )
    env.filters["ternary"] = lambda cond, a, b: a if cond else b
    env.filters["regex_replace"] = (
        lambda s, pat, repl="": re.sub(pat, repl, str(s)))
    env.filters["bool"] = (
        lambda v: str(v).lower() in ("true", "1", "yes", "y"))
    with open(VALUES_TEMPLATE) as f:
        src = f.read()
    base = {
        "id": "mysite",
        "canasta_default_image": "ghcr.io/canastawiki/canasta:3.5.6",
        "argocd_namespace": "argocd",
        "_use_external_db": False,
        "_staging_certs": False,
    }
    base.update(ctx)
    return yaml.safe_load(env.from_string(src).render(**base))


def _helm_render(tmp_path, overlay):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump(overlay))
    out = subprocess.run(
        ["helm", "template", "canasta", CHART, "-f", str(values),
         "--show-only", "templates/deployment-web.yaml",
         "--show-only", "templates/ingress.yaml"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    docs = {d["kind"]: d for d in yaml.safe_load_all(out.stdout) if d}
    return docs["Deployment"], docs["Ingress"]


def _site_server(deployment):
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    return env["MW_SITE_SERVER"]


class TestValuesOverlay:
    def test_skip_tls_writes_ingress_tls_false(self):
        values = _render_overlay(domain_name="example.com", skip_tls=True)
        assert values["ingress"]["tls"] is False
        assert "certManager" not in values["ingress"]

    def test_default_keeps_tls_and_cert_manager(self):
        values = _render_overlay(domain_name="example.com", skip_tls=False)
        assert values["ingress"]["tls"] is True
        assert values["ingress"]["certManager"]["enabled"] is True

    def test_localhost_without_skip_tls_is_unchanged(self):
        values = _render_overlay(domain_name="localhost", skip_tls=False)
        assert "ingress" not in values


class TestRenderedChart:
    def test_skip_tls_serves_http_site_server(self, tmp_path):
        deployment, _ = _helm_render(
            tmp_path,
            _render_overlay(domain_name="example.com", skip_tls=True))
        assert _site_server(deployment) == "http://example.com"

    def test_skip_tls_ingress_has_no_tls_block(self, tmp_path):
        _, ingress = _helm_render(
            tmp_path,
            _render_overlay(domain_name="example.com", skip_tls=True))
        assert "tls" not in ingress["spec"]
        assert ("cert-manager.io/cluster-issuer"
                not in (ingress["metadata"].get("annotations") or {}))

    def test_tls_serves_https_site_server(self, tmp_path):
        deployment, ingress = _helm_render(
            tmp_path,
            _render_overlay(domain_name="example.com", skip_tls=False))
        assert _site_server(deployment) == "https://example.com"
        assert ingress["spec"]["tls"][0]["secretName"] == "canasta-mysite-tls"

    def test_localhost_stays_http(self, tmp_path):
        deployment, ingress = _helm_render(
            tmp_path,
            _render_overlay(domain_name="localhost", skip_tls=False))
        assert _site_server(deployment) == "http://localhost"
        assert "tls" not in ingress["spec"]
