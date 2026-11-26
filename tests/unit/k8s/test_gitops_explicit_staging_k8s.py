"""Kubernetes gitops init and push stage an explicit path list.

Both stage only gitops_k8s_tracked_paths through _stage_tracked_paths.yml,
and that list covers the Kubernetes repo layout (chart, values, Argo CD)
without naming secret or host-local files.
"""

import os

import pytest

from test_gitops_explicit_staging import (
    NEVER_STAGED, TASKS_DIR, VARS, _include, _load, _walk)

K8S_SITES = {
    "init_kubernetes.yml": "gitops_k8s_tracked_paths",
    "push_kubernetes.yml": "gitops_k8s_tracked_paths",
}


class TestNoWholeTreeStaging:
    @pytest.mark.parametrize("site,list_var", sorted(K8S_SITES.items()))
    def test_site_uses_explicit_path_list(self, site, list_var):
        stages = [t for t in _walk(_load(os.path.join(TASKS_DIR, site)))
                  if _include(t) == "_stage_tracked_paths.yml"]
        assert len(stages) == 1, (
            "%s must stage through _stage_tracked_paths.yml" % site)
        assert list_var in str(stages[0].get("vars", {}).get("_stage_paths")), (
            "%s must stage %s" % (site, list_var))


class TestPathLists:
    @pytest.mark.parametrize("key", ["gitops_k8s_tracked_paths"])
    def test_lists_exclude_secret_and_local_files(self, key):
        v = _load(VARS)
        assert not NEVER_STAGED & set(v[key]), key

    def test_lists_cover_repo_layout(self):
        v = _load(VARS)
        common = {".gitignore", ".gitmodules", "hosts", "config",
                  "extensions", "skins", "public_assets"}
        assert common | {".sops.yaml", "Chart.yaml", "templates",
                         "values.yaml", "values.template.yaml", "argocd"} \
            <= set(v["gitops_k8s_tracked_paths"])
