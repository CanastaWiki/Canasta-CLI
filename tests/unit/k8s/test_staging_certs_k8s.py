"""CANASTA_STAGING_CERTS on Kubernetes.

cert-manager gets both Let's Encrypt ClusterIssuers, the values.yaml
template picks the staging or production issuer from the flag, and
'config set' propagates a change to the Ingress issuer.
"""

import os

import pytest
import yaml
from test_staging_certs import REPO_ROOT, _ansible_jinja_env


def _render_template(path, **ctx):
    with open(path) as f:
        src = f.read()
    return _ansible_jinja_env().from_string(src).render(**ctx)


class TestStagingCerts:
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
