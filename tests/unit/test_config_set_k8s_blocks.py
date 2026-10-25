"""config set blocks Compose-only keys on Kubernetes.

On k8s the Ingress terminates TLS and serves 80/443, and Caddy always runs
behind it in http-only mode. The Compose scheme/port side-effects in
_side_effects.yml carry no orchestrator guard, so setting these keys on k8s
would wrongly rewrite MW_SITE_SERVER to a scheme/port nothing serves. They are
blocked up front in _validate_value.yml, which _validate_key.yml includes."""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
VALIDATE_VALUE = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_validate_value.yml",
)


def _tasks():
    with open(VALIDATE_VALUE) as f:
        return yaml.safe_load(f)


def _k8s_block_for(key_substr):
    """The fail task that blocks a key on k8s, matched by its `when`."""
    for t in _tasks():
        if "ansible.builtin.fail" not in t:
            continue
        when = " ".join(str(c) for c in (t.get("when") or []))
        if "instance_is_k8s" in when and key_substr in when:
            return t
    return None


def test_edge_tls_keys_blocked_on_k8s():
    t = _k8s_block_for("TLS_MODE")
    assert t is not None, "config set must block TLS_MODE on Kubernetes"
    msg = t["ansible.builtin.fail"]["msg"]
    assert "Kubernetes" in msg and "cert-manager" in msg
    assert all(k in " ".join(map(str, t["when"])) for k in ("TLS_CERT", "TLS_KEY"))


def test_ports_still_blocked_on_k8s():
    # The existing guard this one mirrors — keep it wired.
    assert _k8s_block_for("HTTP_PORT") is not None
