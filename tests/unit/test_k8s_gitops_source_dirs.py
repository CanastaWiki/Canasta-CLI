"""Regression guards: the K8s gitops repo must track its source-of-truth dirs.

extensions/, skins/, and public_assets/ are the source of truth for the
user-extensions / user-skins / public-assets PVCs
(roles/orchestrator/tasks/k8s_sync_user_dirs.yml), and
config/settings/wikis/<id>/Settings.php is the source of truth for per-wiki
config. An earlier K8s .gitignore appended bare `extensions/`, `skins/`,
`public_assets/`, and `wikis/` lines; the unanchored `wikis/` also matched
config/settings/wikis/, so per-wiki settings and the PVC source dirs were
silently excluded from the gitops repo (data-loss on a fresh clone / join /
DR host). These tests lock in the fix.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INIT_K8S = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "init_kubernetes.yml",
)
REFRESH_GITIGNORE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "refresh_gitignore.yml",
)
FIX_SUBMODULES = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "fix_submodules.yml",
)

# Directories that must never be gitignored on K8s — they are the source of
# truth the repo exists to version-control.
SOURCE_DIRS = ("extensions", "skins", "public_assets", "wikis")


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _k8s_gitignore_content():
    with open(INIT_K8S) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        copy = task.get("ansible.builtin.copy") or task.get("copy")
        if not isinstance(copy, dict):
            continue
        if copy.get("dest", "").endswith("/.gitignore"):
            return copy["content"]
    raise AssertionError("init_kubernetes.yml has no .gitignore-writing task")


class TestK8sGitignoreDoesNotExcludeSourceDirs:
    def test_source_dirs_not_ignored(self):
        """The written .gitignore must not carry a bare `<dir>/` line for any
        source-of-truth directory."""
        lines = [ln.strip() for ln in _k8s_gitignore_content().splitlines()]
        for d in SOURCE_DIRS:
            assert f"{d}/" not in lines, (
                f"init_kubernetes.yml's .gitignore ignores {d}/ — that "
                "directory is the K8s source of truth and must be tracked."
            )


class TestK8sInitConvertsSubmodules:
    def test_init_converts_extensions_and_skins(self):
        """extensions/skins loose git checkouts must be promoted to submodules
        before the initial `git add -A`, else they land as bare gitlinks."""
        with open(INIT_K8S) as f:
            tasks = yaml.safe_load(f)
        includes = [
            t.get("ansible.builtin.include_tasks") or t.get("include_tasks")
            for t in _walk_tasks(tasks)
        ]
        assert includes.count("_convert_submodule.yml") >= 2, (
            "init_kubernetes.yml must convert both extensions/ and skins/ "
            "git repos to submodules (two _convert_submodule.yml includes)."
        )


class TestRefreshGitignoreRemovesObsoleteLines:
    def test_refresh_removes_source_dir_ignores(self):
        """refresh_gitignore.yml (run on every upgrade) must strip the obsolete
        source-dir ignore lines so existing K8s repos self-heal."""
        with open(REFRESH_GITIGNORE) as f:
            tasks = yaml.safe_load(f)
        removed = []
        for task in _walk_tasks(tasks):
            li = task.get("ansible.builtin.lineinfile") or task.get("lineinfile")
            if not isinstance(li, dict) or li.get("state") != "absent":
                continue
            loop = task.get("loop") or []
            if isinstance(loop, list):
                removed.extend(loop)
        for d in SOURCE_DIRS:
            assert d in removed, (
                f"refresh_gitignore.yml does not remove the obsolete {d}/ "
                "ignore line — existing K8s repos would stay broken on upgrade."
            )


class TestFixSubmodulesRunsOnK8s:
    def test_no_kubernetes_refusal(self):
        """fix-submodules must no longer hard-refuse on K8s."""
        with open(FIX_SUBMODULES) as f:
            content = f.read()
        assert "not applicable for the kubernetes" not in content, (
            "fix_submodules.yml still refuses to run on K8s — extensions/skins "
            "are now tracked as submodules on both orchestrators."
        )
