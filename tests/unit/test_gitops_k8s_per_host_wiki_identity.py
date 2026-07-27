"""K8s gitops must not deploy another host's wiki URLs.

configData reaches every host verbatim from the shared values.template.yaml,
which `gitops push` rebuilds from whichever host pushed last. Two entries are
host identity rather than shared config — wikis.yaml (FarmConfigLoader matches
the request host against it) and the Caddyfile (its site blocks gate the request
first). Shared, a host answers its own domain with a blank 200 from Caddy.
The render must overlay this host's own copies.
"""

import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RENDER_K8S = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "render_kubernetes.yml"
)


def _tasks():
    with open(RENDER_K8S) as fh:
        return yaml.safe_load(fh)


def _overlay():
    for t in _tasks():
        sf = t.get("ansible.builtin.set_fact") or {}
        expr = str(sf.get("_rendered_values", ""))
        if "configData" in expr:
            return expr
    return None


def test_wikis_yaml_and_caddyfile_are_overlaid_per_host():
    expr = _overlay()
    assert expr is not None, (
        "render_kubernetes.yml must overlay per-host configData; without it "
        "every host deploys the last pusher's wiki URLs"
    )
    assert "wikis.yaml" in expr, "wikis.yaml must be overlaid from this host"
    assert "Caddyfile" in expr, (
        "the Caddyfile must be overlaid too — Caddy rejects the request before "
        "MediaWiki is reached, so fixing wikis.yaml alone still serves nothing"
    )
    assert "recursive=True" in expr, (
        "must merge into configData, not replace it and drop env/varnish/crowdsec"
    )


def test_overlay_reads_the_local_files_not_the_template():
    tasks = _tasks()
    srcs = [
        (t.get("ansible.builtin.slurp") or {}).get("src", "")
        for t in tasks
    ]
    assert any(s.endswith("/config/wikis.yaml") for s in srcs), (
        "must read this host's own config/wikis.yaml"
    )
    assert any(s.endswith("/config/Caddyfile") for s in srcs), (
        "must read this host's own config/Caddyfile"
    )


def test_overlay_runs_before_the_rendered_file_is_written():
    tasks = _tasks()
    overlay = next(
        (i for i, t in enumerate(tasks)
         if "configData" in str((t.get("ansible.builtin.set_fact") or {}).get(
             "_rendered_values", ""))),
        None,
    )
    write = next(
        (i for i, t in enumerate(tasks)
         if "rendered-values.yaml" in str(
             (t.get("ansible.builtin.copy") or {}).get("dest", ""))),
        None,
    )
    assert overlay is not None and write is not None
    assert overlay < write, "the overlay must land before rendered-values.yaml is written"


def test_absent_files_do_not_blank_the_config():
    # A host without a local Caddyfile must keep whatever the template had,
    # not overwrite it with an empty value.
    expr = _overlay()
    assert "else {}" in expr, (
        "when a local identity file is missing the overlay must contribute "
        "nothing rather than an empty key"
    )
