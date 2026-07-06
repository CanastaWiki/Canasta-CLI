"""C3 scaffolding: SOPS+age secret encryption for K8s gitops. Gated off by
default; the Argo app switches to the CMP plugin source only when enabled; the
age key material is never logged."""
import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INITK8S = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "init_kubernetes.yml")
INITSOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "_init_sops.yml")
GDEFAULTS = os.path.join(REPO_ROOT, "roles", "gitops", "defaults", "main.yml")
BOOTSTRAP = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                         "k8s_argocd_bootstrap.yml")
ODEFAULTS = os.path.join(REPO_ROOT, "roles", "orchestrator", "defaults", "main.yml")
PLUGIN = os.path.join(REPO_ROOT, "roles", "orchestrator", "files",
                      "helm_sops_plugin.yaml")
SIDECAR = os.path.join(REPO_ROOT, "roles", "orchestrator", "templates",
                       "argocd_sops_repo_server_values.yaml.j2")


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


def test_init_sops_restarts_repo_server_after_provision():
    # An optionally-mounted key only reaches a running repo-server on restart,
    # and only on first provision (guarded by the register's changed state).
    c = _read(INITSOPS)
    assert "rollout restart deployment argocd-repo-server" in c
    assert "_sops_age_provision is changed" in c


def test_helm_sops_plugin_is_named_and_uses_argocd_env_prefix():
    p = yaml.safe_load(_read(PLUGIN))
    assert p["kind"] == "ConfigManagementPlugin"
    assert p["metadata"]["name"] == "helm-sops"
    # No spec.version keeps the plugin name exactly "helm-sops" so it matches
    # the Application's spec.source.plugin.name.
    assert "version" not in p["spec"]
    gen = "\n".join(p["spec"]["generate"]["args"])
    # Argo prefixes Application plugin.env with ARGOCD_ENV_ — the values
    # override arrives as ARGOCD_ENV_HELM_ARGS, not HELM_ARGS.
    assert "ARGOCD_ENV_HELM_ARGS" in gen
    assert "$HELM_ARGS" not in gen
    assert "sops -d" in gen
    assert "hosts/*/secrets/*.enc.yaml" in gen


def test_sidecar_values_template():
    # Jinja template; substitute the two vars and parse.
    r = yaml.safe_load(_read(SIDECAR)
                       .replace("{{ sops_version }}", "v3.13.2")
                       .replace("{{ argocd_sops_sidecar_image }}", "img"))
    rs = r["repoServer"]
    sc = [c for c in rs["extraContainers"] if c["name"] == "helm-sops"][0]
    assert sc["command"] == ["/var/run/argocd/argocd-cmp-server"]
    assert sc["securityContext"]["runAsUser"] == 999
    env = {e["name"]: e["value"] for e in sc["env"]}
    assert env["SOPS_AGE_KEY_FILE"] == "/home/argocd/.config/sops/age/keys.txt"
    vols = {v["name"]: v for v in rs["volumes"]}
    # Optional so the repo-server starts before the operator key is provisioned.
    assert vols["sops-age"]["secret"]["optional"] is True
    assert rs["initContainers"][0]["name"] == "install-sops"


def test_sidecar_defaults_present():
    d = yaml.safe_load(_read(ODEFAULTS))
    assert d["sops_version"]
    assert d["argocd_sops_sidecar_image"]


def test_bootstrap_gates_sidecar_provisioning():
    tasks = yaml.safe_load(_read(BOOTSTRAP))
    txt = _read(BOOTSTRAP)
    assert "helm-sops-plugin" in txt
    assert "argocd_sops_repo_server_values.yaml.j2" in txt
    # The sops values file is only appended to the helm args when enabled.
    assert "canasta-argocd-sops-values.yaml" in txt
    assert "gitops_sops_secrets" in txt
