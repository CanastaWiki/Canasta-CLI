"""On Kubernetes the Ingress 'domains' are auto-derived from the farm's wiki
URLs at deploy time (helm_deploy.yml), so the Ingress always routes exactly the
wikis' hostnames — no manual values.yaml upkeep. An operator escape hatch
(extraDomains) is unioned in, and the result is applied as a -f override AFTER
values.yaml so the derived list wins (helm is last-wins for lists)."""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
HELM_DEPLOY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "helm_deploy.yml",
)


def _tasks():
    with open(HELM_DEPLOY) as f:
        return yaml.safe_load(f)


def _task(name_substr):
    for t in _tasks():
        if name_substr in (t.get("name") or ""):
            return t
    raise AssertionError("task %r not found" % name_substr)


def test_derives_hostnames_from_wiki_urls():
    expr = str(_task("Derive ingress domains from wiki URLs")
               ["ansible.builtin.set_fact"])
    assert "wikis" in expr and "url" in expr
    # bare hostname: scheme/path and :port stripped
    assert "/.*$" in expr and ":[0-9]+$" in expr


def test_unions_extra_domains_dedupes_and_falls_back():
    expr = str(_task("Compute final ingress domains")
               ["ansible.builtin.set_fact"])
    assert "extraDomains" in expr          # operator escape hatch
    assert "unique" in expr                # de-dup shared hostnames
    assert "domains" in expr               # else branch: keep existing when no wikis


def test_override_written_and_wired_after_values_yaml():
    write = _task("Write derived ingress-domains override")
    assert "values-domains.yaml" in write["ansible.builtin.copy"]["dest"]
    cmd = _task("Deploy Canasta Helm release")["vars"]["rx_cmd"]
    assert "values.yaml" in cmd and "values-domains.yaml" in cmd
    # the override must come after the base values so its domains list wins
    assert cmd.index("values.yaml") < cmd.index("values-domains.yaml")


def test_gitops_push_also_derives_domains():
    # A GitOps-managed instance deploys from rendered-values.yaml via Argo, so
    # the push must apply the same derivation into the committed values.
    push = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                        "push_kubernetes.yml")
    with open(push) as f:
        tasks = yaml.safe_load(f)
    text = open(push).read()
    assert "Derive ingress domains from wiki URLs" in text
    assert "extraDomains" in text
    merge = [t for t in tasks
             if "Merge fresh configData" in (t.get("name") or "")]
    assert merge, "gitops push must merge derived domains into the values"
    assert "domains" in str(merge[0]["ansible.builtin.set_fact"])
