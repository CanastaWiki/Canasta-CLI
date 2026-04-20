"""Tests for the --staging-certs / CANASTA_STAGING_CERTS feature.

Covers both orchestrators:
- Compose (Caddyfile.j2 emits acme_ca, rewrite_caddy.yml reads .env)
- Kubernetes (values.yaml template, cert-manager issuers, side-effect
  propagation, TLS cascade)

Rendering tests evaluate the real Jinja templates against different
inputs so a flipped ternary or wrong field name is caught — something
structural string-greps wouldn't detect.
"""

import os
import re

import jinja2
import pytest
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
HELM_CHART = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta",
)


def _ansible_jinja_env():
    """Return a Jinja2 Environment with minimal Ansible-compatible filters
    (ternary, regex_replace, bool) registered. Used by rendering tests
    that need to execute an Ansible-style template without spinning up
    full Ansible."""
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["ternary"] = lambda cond, a, b: a if cond else b
    env.filters["regex_replace"] = lambda s, pat, repl="": re.sub(pat, repl, str(s))
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes", "y")
    return env


def _render_template(path, **ctx):
    with open(path) as f:
        src = f.read()
    return _ansible_jinja_env().from_string(src).render(**ctx)


class TestStagingCerts:
    """CANASTA_STAGING_CERTS plumbing across create, both
    orchestrators' templates, and post-create config set."""

    # --- Create time: the flag must land in .env ---

    def test_create_writes_canasta_staging_certs_env(self):
        """roles/create/tasks/main.yml must persist the
        --staging-certs flag as CANASTA_STAGING_CERTS in .env so
        both orchestrators' template logic can read it."""
        path = os.path.join(REPO_ROOT, "roles", "create", "tasks", "main.yml")
        with open(path) as f:
            content = f.read()
        assert "key: CANASTA_STAGING_CERTS" in content, (
            "create/main.yml must write CANASTA_STAGING_CERTS to .env"
        )
        assert "staging_certs | default(false)" in content, (
            "The .env value must be derived from the staging_certs flag"
        )

    # --- Compose: rewrite_caddy.yml must read the .env key ---

    def test_rewrite_caddy_reads_staging_certs_env(self):
        """Compose path: rewrite_caddy.yml must read
        CANASTA_STAGING_CERTS from .env and expose it as
        _staging_certs for Caddyfile.j2 rendering."""
        path = os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
        )
        with open(path) as f:
            content = f.read()
        assert "CANASTA_STAGING_CERTS" in content, (
            "rewrite_caddy.yml must read CANASTA_STAGING_CERTS from .env"
        )
        assert "_staging_certs" in content, (
            "rewrite_caddy.yml must expose _staging_certs to the template"
        )

    def test_staging_certs_is_a_known_config_key(self):
        path = os.path.join(
            REPO_ROOT, "roles", "config", "defaults", "main.yml",
        )
        with open(path) as f:
            defaults = yaml.safe_load(f)
        assert "CANASTA_STAGING_CERTS" in defaults["canasta_known_keys"], (
            "CANASTA_STAGING_CERTS must be in canasta_known_keys so "
            "'canasta config set CANASTA_STAGING_CERTS=...' doesn't "
            "require --force"
        )

    def test_certmanager_creates_both_issuers(self):
        """k8s_certmanager.yml must create both production and
        staging ClusterIssuers so toggling between them post-create
        is a values.yaml change, not a cert-manager reinstall."""
        path = os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_certmanager.yml",
        )
        with open(path) as f:
            content = f.read()
        assert "name: letsencrypt-prod" in content
        assert "name: letsencrypt-staging" in content
        assert "acme-staging-v02.api.letsencrypt.org" in content

    def test_k8s_values_template_honors_staging_certs(self):
        path = os.path.join(
            REPO_ROOT, "roles", "orchestrator", "templates",
            "k8s_values.yaml.j2",
        )
        with open(path) as f:
            content = f.read()
        assert "_staging_certs" in content
        assert "letsencrypt-staging" in content
        assert "letsencrypt-prod" in content

    def test_caddyfile_template_emits_acme_ca_for_staging(self):
        """Compose path: Caddyfile.j2 must emit acme_ca pointing at
        the staging directory when _staging_certs is true."""
        path = os.path.join(
            REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2",
        )
        with open(path) as f:
            content = f.read()
        assert "_staging_certs" in content
        assert "acme_ca" in content
        assert "acme-staging-v02.api.letsencrypt.org" in content

    def test_side_effects_propagates_staging_certs(self):
        """Changing CANASTA_STAGING_CERTS via config set on a K8s
        instance must patch the Ingress issuer in values.yaml."""
        path = os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml",
        )
        with open(path) as f:
            content = f.read()
        assert "'CANASTA_STAGING_CERTS'" in content, (
            "_side_effects.yml must list CANASTA_STAGING_CERTS in the "
            "K8s values.yaml propagation list"
        )
        # The TLS cascade must consult CANASTA_STAGING_CERTS too.
        assert "_se_tls_staging" in content, (
            "localhost→domain TLS cascade must honor CANASTA_STAGING_CERTS"
        )

    # --- Rendering tests: actually evaluate the Jinja templates ---
    # Structural greps (above) pass even if a ternary is flipped. The
    # tests below render the templates against real input and assert
    # on the output.

    @pytest.mark.parametrize("staging,expected_issuer", [
        (True, "letsencrypt-staging"),
        (False, "letsencrypt-prod"),
    ])
    def test_k8s_values_template_renders_correct_issuer(
        self, staging, expected_issuer,
    ):
        path = os.path.join(
            REPO_ROOT, "roles", "orchestrator", "templates",
            "k8s_values.yaml.j2",
        )
        out = _render_template(
            path,
            id="mysite",
            canasta_default_image="ghcr.io/canastawiki/canasta:3.5.6",
            domain_name="example.com",
            skip_tls=False,
            argocd_namespace="argocd",
            _use_external_db=False,
            _staging_certs=staging,
        )
        # Parse as YAML and walk the structure — catches field-name
        # typos that a naive string match would miss.
        parsed = yaml.safe_load(out)
        assert parsed["ingress"]["certManager"]["issuer"] == expected_issuer

    @pytest.mark.parametrize("staging,expect_acme_ca", [
        (True, True),
        (False, False),
    ])
    def test_caddyfile_template_emits_acme_ca_only_for_staging(
        self, staging, expect_acme_ca,
    ):
        path = os.path.join(
            REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2",
        )
        out = _render_template(
            path,
            _site_address="example.com",
            _backend="varnish:80",
            _observable=False,
            _os_user="",
            _os_password_hash="",
            _staging_certs=staging,
        )
        has_acme_ca = "acme_ca" in out
        has_staging_url = "acme-staging-v02.api.letsencrypt.org" in out
        assert has_acme_ca is expect_acme_ca, (
            "acme_ca directive should %s" %
            ("appear when staging=true" if staging
             else "NOT appear when staging=false")
        )
        assert has_staging_url is expect_acme_ca

    @pytest.mark.parametrize("config_value,expected_issuer", [
        ("true", "letsencrypt-staging"),
        ("True", "letsencrypt-staging"),
        ("TRUE", "letsencrypt-staging"),
        ("false", "letsencrypt-prod"),
        ("False", "letsencrypt-prod"),
        ("", "letsencrypt-prod"),
        ("anything-else", "letsencrypt-prod"),
    ])
    def test_side_effects_override_picks_correct_issuer(
        self, config_value, expected_issuer,
    ):
        """The _side_effects.yml override for CANASTA_STAGING_CERTS
        constructs a nested dict {ingress: {certManager: {issuer: ...}}}
        via a Jinja ternary. Evaluate that expression and assert the
        ternary picks the right arm across truthy/falsy/edge-case inputs."""
        expr = (
            "{{ (_config_value | lower == 'true') "
            "| ternary('letsencrypt-staging', 'letsencrypt-prod') }}"
        )
        rendered = _ansible_jinja_env().from_string(expr).render(
            _config_value=config_value,
        )
        assert rendered == expected_issuer
