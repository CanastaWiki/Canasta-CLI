"""CrowdSec enrollment on Kubernetes says when the key still needs a push.

On a gitops Kubernetes instance Argo CD deploys the committed render, so a
freshly stored bouncer key reaches Caddy only after 'canasta gitops push'
unless the committed Caddyfile already has the bouncer and the key's
Secret is applied by the CLI rather than owned by Argo CD (SOPS).
"""

from test_crowdsec_enroll_reentry import _run


PUSH_HINT = ("CrowdSec bouncer 'canasta-caddy' is registered for instance "
             "'demo' and its key is stored, but Caddy does not use it until "
             "it is in the gitops repo. Run 'canasta gitops push -i demo' to "
             "activate it")


CADDY_WITHOUT_BOUNCER = "{\n    email admin@example.com\n}\n"
CADDY_WITH_BOUNCER = (
    "{\n    order crowdsec first\n    crowdsec {\n"
    "        api_url http://crowdsec:8080\n"
    "        api_key {env.CROWDSEC_BOUNCER_API_KEY}\n"
    "        ticker_interval 15s\n    }\n}\n")


def test_gitops_kubernetes_enroll_says_to_push_not_active(tmp_path):
    # The committed render predates CrowdSec, so Argo CD deploys a Caddyfile
    # with no bouncer until the next push.
    proc, inst = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n",
                      k8s=True, gitops=True,
                      committed_caddy=CADDY_WITHOUT_BOUNCER)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "CROWDSEC_BOUNCER_API_KEY=fresh-key" in (inst / ".env").read_text()
    assert out.count(PUSH_HINT) == 1, out
    assert "is enrolled and active for" not in out
    assert "restarting to activate it" not in out


def test_kubernetes_without_gitops_reports_active(tmp_path):
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n", k8s=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count("is enrolled and active for") == 1, out
    assert "gitops push" not in out


def test_gitops_kubernetes_with_committed_bouncer_reports_active(tmp_path):
    # A re-enroll: the committed Caddyfile already has the bouncer and the
    # key lives in a Secret the CLI applies, so Caddy uses the new key now.
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n",
                   k8s=True, gitops=True, committed_caddy=CADDY_WITH_BOUNCER)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count("is enrolled and active for") == 1, out
    assert "gitops push" not in out


def test_gitops_kubernetes_with_sops_needs_a_push(tmp_path):
    # Argo CD owns the key's Secret, so the new key reaches Caddy only once
    # it is pushed, even with the bouncer already in the committed Caddyfile.
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n",
                   k8s=True, gitops=True, committed_caddy=CADDY_WITH_BOUNCER,
                   sops=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count(PUSH_HINT) == 1, out
    assert "is enrolled and active for" not in out


def test_gitops_kubernetes_without_a_committed_render_needs_a_push(tmp_path):
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n",
                   k8s=True, gitops=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count(PUSH_HINT) == 1, out
