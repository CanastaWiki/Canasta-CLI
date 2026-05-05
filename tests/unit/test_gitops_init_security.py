"""Security-regression guards for gitops init.

The K8s gitops init writes a .gitignore on the target instance dir.
A regression where that file omits .gitops-deploy-key causes the SSH
deploy private key (with write access to the gitops repo) to be
committed into that same repo on the next push, exposing it to every
reader of the repo. See issue #505.

These tests parse init_kubernetes.yml's .gitignore-writing task and
assert both that it sources the canonical gitignore.default file and
that the canonical file still contains the security-critical entries.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INIT_K8S = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "init_kubernetes.yml",
)
GITIGNORE_DEFAULT = os.path.join(
    REPO_ROOT, "roles", "gitops", "files", "gitignore.default",
)


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _find_gitignore_task():
    with open(INIT_K8S) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        copy = task.get("ansible.builtin.copy") or task.get("copy")
        if not isinstance(copy, dict):
            continue
        dest = copy.get("dest", "")
        if dest.endswith("/.gitignore"):
            return copy
    raise AssertionError(
        "init_kubernetes.yml has no task writing .gitignore — "
        "the security-regression guard cannot run"
    )


class TestGitopsDeployKeyNotCommitted:
    """The K8s gitops init MUST NOT drop .gitops-deploy-key from the
    deployed .gitignore. This caused the deploy private key to be
    committed into the gitops repo (#505) — anyone with read access
    to the repo could extract the key and push."""

    def test_canonical_gitignore_default_contains_deploy_key(self):
        """The shared list must keep .gitops-deploy-key. The K8s and
        Compose paths both depend on it."""
        with open(GITIGNORE_DEFAULT) as f:
            content = f.read()
        assert ".gitops-deploy-key" in content, (
            "gitignore.default no longer ignores .gitops-deploy-key — "
            "without that entry the SSH deploy private key gets "
            "committed into the gitops repo on the first push (#505)."
        )

    def test_k8s_init_sources_canonical_gitignore_default(self):
        """The K8s init must compose .gitignore from gitignore.default
        rather than inlining its own list — past inlines drifted and
        dropped security-critical entries (#505)."""
        copy = _find_gitignore_task()
        content = copy.get("content") or ""
        # The Jinja lookup is the canonical wiring; it pulls
        # gitignore.default verbatim. If a future edit drops the
        # lookup and re-inlines the list, the next test will catch
        # the .gitops-deploy-key omission directly — this assertion
        # makes the structural intent explicit.
        assert "gitignore.default" in content, (
            "init_kubernetes.yml's .gitignore-writing task no longer "
            "sources roles/gitops/files/gitignore.default. Reverting "
            "to an inline list invites the same drift that caused "
            "#505 — re-source the canonical file."
        )

    def test_k8s_deployed_gitignore_includes_deploy_key(self):
        """Direct check: whatever the K8s path ends up writing must
        include .gitops-deploy-key. Belt-and-suspenders against any
        future templating change that bypasses the canonical file."""
        copy = _find_gitignore_task()
        content = copy.get("content") or ""

        # Resolve the lookup (or any other reference to the canonical
        # file) by inlining the file contents. If the task drops
        # gitignore.default entirely, the inline content is taken as
        # authoritative.
        if "gitignore.default" in content:
            with open(GITIGNORE_DEFAULT) as f:
                content = content + "\n" + f.read()

        assert ".gitops-deploy-key" in content, (
            "K8s gitops init's .gitignore would omit .gitops-deploy-key. "
            "The SSH deploy private key would be committed into the "
            "gitops repo on the next push (#505)."
        )

    def test_k8s_deployed_gitignore_includes_other_credential_entries(self):
        """admin-password_* and config/wikis.yaml are also security-
        relevant — admin-password_* leaks the wiki admin password,
        config/wikis.yaml can carry per-wiki secrets in some
        deployments. Any path that bypasses gitignore.default must
        also keep these."""
        copy = _find_gitignore_task()
        content = copy.get("content") or ""
        if "gitignore.default" in content:
            with open(GITIGNORE_DEFAULT) as f:
                content = content + "\n" + f.read()

        for required in ("admin-password_*", "config/wikis.yaml"):
            assert required in content, (
                "K8s gitops init's .gitignore would omit %r — past "
                "inline-list drift dropped this; keep the canonical "
                "gitignore.default as the single source." % required
            )
