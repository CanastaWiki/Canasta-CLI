"""On Kubernetes, Caddy sits behind the ingress (Traefik terminates TLS and
forwards plain HTTP), so the generated Caddyfile must always be http-only — no
HTTP->HTTPS redirect — or it loops behind the ingress. rewrite_caddy.yml must
therefore force _http_only on K8s regardless of the CADDY_AUTO_HTTPS value a
cross-orchestrator restore may leave in .env. On Compose (Caddy is the TLS
edge) CADDY_AUTO_HTTPS still drives it."""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _orchestrator import TEMPLAR_VARS

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
REWRITE_CADDY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
)


def _http_only_expr():
    """The actual _http_only Jinja expression from rewrite_caddy.yml."""
    with open(REWRITE_CADDY) as f:
        tasks = yaml.safe_load(f)
    for t in tasks:
        sf = t.get("ansible.builtin.set_fact") or {}
        if "_http_only" in sf:
            return sf["_http_only"]
    raise AssertionError("_http_only set_fact not found in rewrite_caddy.yml")


def _eval(orchestrator, caddy_auto_https):
    variables = {}
    if caddy_auto_https is not None:
        variables["CADDY_AUTO_HTTPS"] = caddy_auto_https
    templar = Templar(loader=DataLoader(), variables=dict(
        TEMPLAR_VARS,
        instance_orchestrator=orchestrator,
        _env_caddy={"variables": variables},
    ))
    return str(templar.template(trust_as_template(_http_only_expr()))).strip() == "True"


def test_k8s_forces_http_only_even_without_caddy_auto_https():
    # The exact cross-orchestrator-restore case: k8s instance, .env has no
    # CADDY_AUTO_HTTPS (the Compose backup's .env clobbered the create default).
    assert _eval("kubernetes", None) is True
    assert _eval("k8s", None) is True


def test_k8s_forces_http_only_even_when_auto_https_on():
    assert _eval("kubernetes", "on") is True


def test_compose_still_driven_by_caddy_auto_https():
    assert _eval("compose", "off") is True     # http-only
    assert _eval("compose", "on") is False      # Caddy is the TLS edge
    assert _eval("compose", None) is False      # default 'on'


def test_expression_references_the_orchestrator():
    # Guard against the k8s clause being dropped in a future refactor.
    assert "instance_is_k8s" in _http_only_expr()
