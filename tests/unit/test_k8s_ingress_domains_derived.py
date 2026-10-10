"""On Kubernetes the Ingress 'domains' are auto-derived from the farm's wiki
URLs at deploy time (helm_deploy.yml), so the Ingress always routes exactly the
wikis' hostnames — no manual values.yaml upkeep. An operator escape hatch
(extraDomains) is unioned in, and the result is applied as a -f override AFTER
values.yaml so the derived list wins (helm is last-wins for lists).

A gitops instance is deployed by Argo CD from hosts/<host>/rendered-values.yaml,
so the render derives the same list from the host's own wikis.yaml; host vars'
domains, written at init, never followed a wiki added later. One filter does
the derivation everywhere, so the two deploy paths cannot drift apart."""

import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "filter_plugins"))
from canasta_gitops import canasta_wiki_ingress_domains as domains  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
HELM_DEPLOY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "helm_deploy.yml",
)


def _tasks():
    with open(HELM_DEPLOY) as f:
        return yaml.safe_load(f)


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _task(name_substr):
    # The deploy is nested in a block whose rescue reports cluster state
    # on a failed rollout, so look through nested tasks too.
    for t in _walk(_tasks()):
        if name_substr in (t.get("name") or ""):
            return t
    raise AssertionError("task %r not found" % name_substr)


def _helm_command():
    """The assembled `helm upgrade` command, wherever the task sits."""
    for t in _walk(_tasks()):
        cmd = (t.get("vars") or {}).get("rx_cmd", "")
        if "helm upgrade --install" in cmd:
            return cmd
    raise AssertionError("no task assembles the helm upgrade command")


WIKIS = (
    "wikis:\n"
    "- id: main\n  url: wiki.example.com\n"
    "- id: docs\n  url: wiki.example.com/docs\n"
    "- id: notes\n  url: notes.example.com:8443/n\n"
)


def test_derives_hostnames_from_wiki_urls():
    # path and :port stripped, a shared host listed once
    assert domains(WIKIS) == ["wiki.example.com", "notes.example.com"]


def test_unions_extra_domains_dedupes_and_falls_back():
    assert domains(WIKIS, ["alias.example.com", "wiki.example.com"]) == [
        "wiki.example.com", "notes.example.com", "alias.example.com"]
    # no wikis.yaml, or no urls in it: keep the existing list
    assert domains("", ["x.example.com"], ["old.example.com"]) == [
        "old.example.com"]
    assert domains("wikis: []\n", [], ["old.example.com"]) == [
        "old.example.com"]
    assert domains("wikis: [", [], ["old.example.com"]) == ["old.example.com"]


def test_every_deploy_path_uses_the_one_derivation():
    for path in (
            HELM_DEPLOY,
            os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                         "push_kubernetes.yml"),
            os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                         "render_kubernetes.yml")):
        with open(path) as f:
            text = f.read()
        assert "canasta_wiki_ingress_domains(" in text, path
        assert "regex_replace(':[0-9]+$'" not in text, path


def test_render_derives_this_hosts_domains_from_its_wikis_yaml():
    render = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                          "render_kubernetes.yml")
    with open(render) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    derive = names.index("Derive this host's ingress domains from its wikis.yaml")
    assert names.index("Read this host's wikis.yaml") < derive
    assert derive < names.index("Write rendered-values.yaml")
    expr = str(tasks[derive]["ansible.builtin.set_fact"])
    assert "_render_local_wikis" in expr and "extraDomains" in expr


def test_override_written_and_wired_after_values_yaml():
    write = _task("Write derived ingress-domains override")
    assert "values-domains.yaml" in write["ansible.builtin.copy"]["dest"]
    cmd = _helm_command()
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
