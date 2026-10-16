"""Installing Argo CD refreshes the argo Helm repository first.

The install pins the chart version, and an argo repository the host already
has keeps the index from when it was added, which may predate the pin.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
BOOTSTRAP = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                         "k8s_argocd_bootstrap.yml")


def _install_tasks():
    with open(BOOTSTRAP) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in tasks if t.get("name") == "Install Argo CD")["block"]


def test_repository_is_refreshed_before_the_pinned_install():
    names = [t.get("name") for t in _install_tasks()]
    repo = next(t for t in _install_tasks()
                if "kubernetes.core.helm_repository" in t)
    assert repo["kubernetes.core.helm_repository"]["force_update"] is True
    install = next(t for t in _install_tasks()
                   if "--version" in str(t))
    assert names.index(repo["name"]) < names.index(install["name"])
