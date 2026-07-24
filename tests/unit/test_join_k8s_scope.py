"""Guard: gitops join (Kubernetes) must stage only its own artifacts, not the
fresh instance's untracked create-time files.

A K8s join's intended artifacts are the host registration, this host's vars,
its rendered values, and the Argo CD Application manifest. A blind `git add -A`
would also sweep in untracked create-time files (values.yaml and friends) and
push them into the shared repo.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JOIN = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "join_kubernetes.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _cmd_text(t):
    c = t.get("ansible.builtin.command") or t.get("command") or {}
    if not isinstance(c, dict):
        return str(c)
    if c.get("argv"):
        return " ".join(str(a) for a in c["argv"])
    return c.get("cmd", "")


class TestJoinK8sStagingScope:
    def test_no_whole_tree_add(self):
        offenders = [
            _cmd_text(t) for t in _walk(_load(JOIN))
            if "git add -A" in _cmd_text(t) or "git add ." in _cmd_text(t)
        ]
        assert not offenders, (
            "gitops join (K8s) must not `git add -A` — it sweeps the fresh "
            "instance's untracked create-time files into the shared repo: %r"
            % offenders)

    def test_stages_intended_artifacts(self):
        adds = " ".join(_cmd_text(t) for t in _walk(_load(JOIN))
                        if "git add" in _cmd_text(t))
        assert adds, "join_kubernetes.yml must stage its artifacts"
        for artifact in ("hosts/hosts.yaml", "vars.yaml",
                         "rendered-values.yaml", "argocd/application.yaml"):
            assert artifact in adds, (
                "K8s join must stage %s explicitly: %r" % (artifact, adds))
