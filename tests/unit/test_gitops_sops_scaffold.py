"""C3 scaffolding: SOPS+age secret encryption for K8s gitops. Gated off by
default; the Argo app switches to the CMP plugin source only when enabled; the
age key material is never logged."""
import os

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ODEFAULTS = os.path.join(REPO_ROOT, "roles", "orchestrator", "defaults", "main.yml")


def _read(p):
    with open(p) as f:
        return f.read()


def _flatten(tasks):
    """Yield tasks recursively, descending into block/rescue/always."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        nested = False
        for k in ("block", "rescue", "always"):
            if k in t:
                nested = True
                yield from _flatten(t[k])
        if not nested:
            yield t


def test_migration_replaceholder_is_case_insensitive():
    c = _read(os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                           "_migrate_reclassified_secrets.yml"))
    assert "(?i)^{{ item }}=" in c
