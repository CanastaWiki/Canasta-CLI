"""Resources Argo CD creates from the chart can be adopted by `helm upgrade`.

Argo CD renders the chart with `helm template` and applies the output
itself, so a resource it creates first carries only what the chart renders.
`helm upgrade` refuses to take over a resource without Helm's release
ownership metadata, which failed every later start/restart of a gitops
instance on any resource a push + sync had added.
"""

import glob
import os
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHART = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta")

# Every optional feature on, so every template renders.
ALL_ON = {
    "instance": {"id": "demo"},
    "domains": ["wiki.example.com"],
    "db": {"enabled": True, "wikiUser": "mediawiki"},
    "crowdsec": {"enabled": True},
    "varnish": {"enabled": True},
    "elasticsearch": {"enabled": True},
    "ingress": {"enabled": True, "annotations": {"example.com/x": "kept"}},
    "argocd": {"repoURL": "git@example.com:org/repo.git"},
    "configData": {
        "caddy": {"Caddyfile": "x"},
        "env": {"A": "1"},
        "web": {"f": "x", "wikis.yaml": "wikis:\n- id: main\n"},
        "db": {"my.cnf": "[mysqld]"},
        "varnish": {"default.vcl": "x"},
        "crowdsec": {"whitelist.yaml": "x"},
    },
    "sidecars": [{
        "name": "cache",
        "image": "redis:7",
        "ports": [6379],
        "volumes": [{"name": "data", "path": "/data", "persistent": True}],
        "files": [{"path": "/etc/cache.conf", "content": "maxmemory 64mb"}],
    }],
}

EXPECTED_KINDS = {
    "ConfigMap", "PersistentVolumeClaim", "Service", "Deployment",
    "StatefulSet", "Ingress", "Application",
}


def _render(tmp_path, release, namespace=None):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump(ALL_ON))
    cmd = ["helm", "template", release, CHART, "-f", str(values)]
    if namespace:
        cmd += ["--namespace", namespace]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _ownership(doc):
    meta = doc["metadata"]
    labels = meta.get("labels") or {}
    annotations = meta.get("annotations") or {}
    return (labels.get("app.kubernetes.io/managed-by"),
            annotations.get("meta.helm.sh/release-name"),
            annotations.get("meta.helm.sh/release-namespace"))


class TestRenderedOwnership:
    def test_every_resource_names_the_direct_deploy_release(self, tmp_path):
        # helm_deploy.yml installs release canasta-<id> into canasta-<id>.
        docs = _render(tmp_path, "canasta-demo", "canasta-demo")
        assert EXPECTED_KINDS <= {d["kind"] for d in docs}
        wrong = {
            "%s/%s" % (d["kind"], d["metadata"]["name"]): _ownership(d)
            for d in docs
            if _ownership(d) != ("Helm", "canasta-demo", "canasta-demo")
        }
        assert not wrong, wrong

    def test_the_helm_sops_plugin_render_matches(self, tmp_path):
        # The plugin runs `helm template "$ARGOCD_APP_NAME" .` with no
        # --namespace, so the namespace must not come from .Release.
        direct = _render(tmp_path, "canasta-demo", "canasta-demo")
        plugin = _render(tmp_path, "canasta-demo")
        assert [_ownership(d) for d in plugin] == [
            _ownership(d) for d in direct]

    def test_existing_annotations_are_kept(self, tmp_path):
        docs = _render(tmp_path, "canasta-demo", "canasta-demo")
        ingress = next(d for d in docs if d["kind"] == "Ingress")
        assert ingress["metadata"]["annotations"]["example.com/x"] == "kept"
        pvc = next(d for d in docs if d["kind"] == "PersistentVolumeClaim"
                   and d["metadata"]["name"] == "canasta-demo-cache-data")
        assert pvc["metadata"]["annotations"][
            "helm.sh/resource-policy"] == "keep"

    def test_pod_templates_are_unchanged(self, tmp_path):
        # Ownership belongs on the resource; on a pod template it would
        # roll every pod on the first deploy of this chart.
        docs = _render(tmp_path, "canasta-demo", "canasta-demo")
        for d in docs:
            pod = (d.get("spec") or {}).get("template") or {}
            annotations = (pod.get("metadata") or {}).get("annotations") or {}
            assert not any(k.startswith("meta.helm.sh/") for k in annotations)


class TestEveryTemplate:
    def test_every_resource_template_includes_the_annotations(self):
        # Guards a template added later, whatever its feature gate.
        missing = []
        for path in sorted(glob.glob(os.path.join(CHART, "templates", "*.yaml"))):
            with open(path) as f:
                text = f.read()
            kinds = text.count("\nkind: ") + text.startswith("kind: ")
            includes = text.count('include "canasta.ownershipAnnotations"')
            if kinds != includes:
                missing.append((os.path.basename(path), kinds, includes))
        assert not missing, missing
