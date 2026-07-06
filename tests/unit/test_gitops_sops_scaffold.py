"""C3 scaffolding: SOPS+age secret encryption for K8s gitops. Gated off by
default; the Argo app switches to the CMP plugin source only when enabled; the
age key material is never logged."""
import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INITK8S = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "init_kubernetes.yml")
INITSOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "_init_sops.yml")
GDEFAULTS = os.path.join(REPO_ROOT, "roles", "gitops", "defaults", "main.yml")


def _read(p):
    with open(p) as f:
        return f.read()


def test_sops_off_by_default():
    assert yaml.safe_load(_read(GDEFAULTS))["gitops_sops_secrets"] is False


def test_argo_app_source_is_gated_plugin_vs_helm():
    c = _read(INITK8S)
    assert "gitops_sops_secrets | default(false) | bool" in c
    # CMP plugin branch (enabled) + native helm branch (default) both present.
    assert "plugin:" in c and "helm-sops" in c
    assert "valueFiles:" in c


def test_init_kubernetes_gates_sops_include():
    inc = [t for t in yaml.safe_load(_read(INITK8S))
           if "_init_sops.yml" in str(t.get("ansible.builtin.include_tasks", ""))]
    assert inc, "init_kubernetes must include _init_sops.yml"
    assert "gitops_sops_secrets" in str(inc[0].get("when", ""))


def test_init_sops_structure():
    c = _read(INITSOPS)
    assert "age-keygen" in c
    # Encrypt only the Secret payload; keep apiVersion/kind/metadata readable.
    assert "encrypted_regex" in c and "(data|stringData)" in c
    assert "sops-age" in c


def test_init_sops_never_logs_key_material():
    for t in yaml.safe_load(_read(INITSOPS)):
        name = (t.get("name") or "").lower()
        if any(s in name for s in ("age keypair", "public recipient",
                                   "sops-age secret")):
            assert t.get("no_log") is True, "%r must be no_log" % name
