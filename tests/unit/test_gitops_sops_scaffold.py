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
WRITESECRETS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                            "_write_sops_secrets.yml")
WRITEONE = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                        "_write_one_sops_secret.yml")
PUSHK8S = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                       "push_kubernetes.yml")
TOOLVERSIONS = os.path.join(REPO_ROOT, "vars", "tool_versions.yml")
ENSURE = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                      "k8s_ensure_sops_sidecar.yml")
CMDDEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


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


def test_init_sops_structure():
    c = _read(INITSOPS)
    assert "age-keygen" in c
    # Encrypt only the Secret payload; keep apiVersion/kind/metadata readable.
    assert "encrypted_regex" in c and "(data|stringData)" in c
    assert "sops-age" in c


def test_init_sops_age_commands_use_argv_for_space_safe_paths():
    # The config dir can contain spaces (macOS "~/Library/Application
    # Support/canasta"), so age-keygen must run via argv, never a shell string.
    for t in _flatten(yaml.safe_load(_read(INITSOPS))):
        cmd = t.get("ansible.builtin.command")
        if isinstance(cmd, str) and "age-keygen" in cmd and "--version" not in cmd:
            raise AssertionError("age-keygen path command must use argv: %r" % cmd)
    txt = _read(INITSOPS)
    assert "argv:" in txt and "- age-keygen" in txt


def test_init_sops_key_is_cluster_global_on_controller():
    c = _read(INITSOPS)
    # Canonical key lives on the controller, keyed by host = one per cluster.
    assert "sops/{{ host_name }}.age" in c
    assert "CANASTA_CONFIG_DIR" in c
    # --key is the DR import/export channel, not the runtime location.
    assert "{{ key }}.age" in c or "key ~ '.age'" in c


def test_init_sops_never_logs_key_material():
    # Tasks that read/write/generate key CONTENT must be no_log (not the
    # path set_fact / stat / mkdir, which only touch the location).
    sensitive = ("import the supplied", "generate the canonical operator age key",
                 "export the operator age key", "public recipient",
                 "sops-age secret")
    seen = 0
    for t in _flatten(yaml.safe_load(_read(INITSOPS))):
        name = (t.get("name") or "").lower()
        if any(s in name for s in sensitive):
            assert t.get("no_log") is True, "%r must be no_log" % name
            seen += 1
    assert seen >= 4, "expected the key-material tasks to be present + checked"


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
    # set -e so a failed helm template aborts (else Argo prunes the instance).
    assert "set -e" in gen
    # Decrypt only THIS host's secrets, not every host's (host-blind glob bug).
    assert "$ARGOCD_ENV_SECRETS_DIR" in gen
    assert "hosts/*/secrets" not in gen


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


def test_sidecar_config_is_play_global():
    # sops version, age version, and the sidecar image are all play-global
    # (one definition), so the gitops role's cross-role include of the sidecar
    # task resolves them — role defaults would be out of scope there.
    versions = yaml.safe_load(_read(TOOLVERSIONS))
    assert versions["sops_version"]
    assert versions["age_version"]
    assert versions["argocd_sops_sidecar_image"]


def test_write_sops_secrets_captures_only_opaque_unowned():
    c = _read(WRITESECRETS)
    # Only type=Opaque Secrets are versioned (excludes helm-release/TLS/SA-token).
    assert "equalto', 'Opaque'" in c or "equalto\", \"Opaque" in c
    # And only unowned ones — drops cert-manager's ACME key Secrets (owned by
    # a Certificate) and any other controller-managed Opaque Secret.
    assert "rejectattr('metadata.ownerReferences', 'defined')" in c
    assert "k8s_info" in c
    # Prunes manifests for Secrets no longer present.
    assert "Prune encrypted manifests" in c


def test_application_scopes_secrets_dir_to_host():
    c = _read(INITK8S)
    # Plugin env carries a per-host SECRETS_DIR so the CMP decrypts only this
    # host's secrets.
    assert "SECRETS_DIR" in c
    assert "hosts/{{ host_name }}/secrets" in c


def test_join_wires_sops():
    c = _read(os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                           "join_kubernetes.yml"))
    # Join detects a .sops.yaml clone, enables SOPS, provisions decryption +
    # the sidecar, and writes a plugin-source Application scoped to this host.
    assert ".sops.yaml" in c
    assert "_join_sops.yml" in c
    assert "k8s_ensure_sops_sidecar.yml" in c
    assert "SECRETS_DIR" in c


def test_join_sops_verifies_key_and_provisions_secret():
    c = _read(os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                           "_join_sops.yml"))
    # Refuses without the operator key; verifies it matches the repo recipient;
    # provisions the sops-age Secret. Uses argv for the (possibly spaced) path.
    assert "does not match this repository" in c
    assert "{{ key }}.age" in c
    assert "sops-age" in c
    assert "argv:" in c


def test_reinit_cleanup_removes_sops_marker():
    c = _read(os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                           "_reinit_cleanup.yml"))
    assert ".sops.yaml" in c


def test_prune_guarded_against_empty_namespace():
    c = _read(WRITESECRETS)
    # Prune only runs when the namespace actually returned Secrets, so an
    # empty/absent namespace can't wipe every DR copy from git.
    assert "_sops_secrets | length > 0" in c


def test_skip_gate_requires_encrypted_file():
    c = _read(WRITEONE)
    # A hash match is only trusted when the file is actually encrypted.
    assert "_sec_encrypted" in c and "ENC[" in c
    # A failed encryption removes the cleartext rather than leaving it.
    assert "rescue:" in c


def test_sidecar_verifies_sops_checksum():
    tv = yaml.safe_load(_read(TOOLVERSIONS))
    assert tv["sops_sha256"]["amd64"] and tv["sops_sha256"]["arm64"]
    t = _read(SIDECAR)
    assert "sha256sum -c" in t and "sops_sha256" in t


def test_ensure_sidecar_pins_chart_version():
    c = _read(ENSURE)
    assert "get metadata argocd" in c
    assert "--version {{ _argocd_chart_version }}" in c


def test_migration_replaceholder_is_case_insensitive():
    c = _read(os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                           "_migrate_reclassified_secrets.yml"))
    assert "(?i)^{{ item }}=" in c


def test_write_one_sops_secret_hashes_and_encrypts():
    c = _read(WRITEONE)
    # Cleartext hash annotation (metadata stays readable under SOPS) drives
    # skip-if-unchanged so encrypted blobs don't churn.
    assert "canasta.io/gitops-secret-hash" in c
    assert "hash('sha256')" in c
    assert "sops -e -i" in c
    # The cleartext-manifest write must never be logged.
    wrote_clear = [t for t in _flatten(yaml.safe_load(c))
                   if "cleartext secret manifest" in (t.get("name") or "").lower()]
    assert wrote_clear and wrote_clear[0].get("no_log") is True


def test_push_gates_secret_capture_on_sops_marker():
    c = _read(PUSHK8S)
    assert "_write_sops_secrets.yml" in c
    # Self-describing: push keys off the .sops.yaml marker, not a flag/var,
    # so it needs no --encrypt-secrets of its own.
    assert "_push_sops_marker.stat.exists" in c
    assert ".sops.yaml" in c
    # host_name is resolved from .gitops-host before capture.
    assert ".gitops-host" in c


def test_install_supports_sops_toolchain():
    inst = _read(os.path.join(REPO_ROOT, "playbooks", "install.yml"))
    assert "'sops' in _install_packages" in inst
    assert "sops_age.yml" in inst
    task = _read(os.path.join(REPO_ROOT, "roles", "install", "tasks",
                              "sops_age.yml"))
    # Installs both binaries from pinned release versions, idempotently.
    assert "getsops/sops/releases" in task
    assert "FiloSottile/age/releases" in task
    assert "age-keygen" in task
    assert "sops_version" in task and "age_version" in task


def test_encrypt_secrets_flag_drives_the_gate():
    # The CLI flag exists on gitops init and maps to gitops_sops_secrets.
    cmds = yaml.safe_load(_read(CMDDEFS))["commands"]
    gi = [c for c in cmds if c["name"] == "gitops_init"][0]
    assert any(p["name"] == "encrypt_secrets" for p in gi["parameters"])
    ik = _read(INITK8S)
    assert "encrypt_secrets | default(false) | bool" in ik
    assert "gitops_sops_secrets:" in ik


def test_sidecar_ensured_at_init_not_create():
    # The sidecar is overlaid at gitops init via helm upgrade --reuse-values,
    # not baked into the create-time Argo CD install.
    ensure = _read(ENSURE)
    assert "helm-sops-plugin" in ensure
    assert "argocd_sops_repo_server_values.yaml.j2" in ensure
    assert "--reuse-values" in ensure
    ik = _read(INITK8S)
    assert "k8s_ensure_sops_sidecar.yml" in ik
    # create's Argo CD install must NOT carry the sidecar values anymore.
    assert "argocd_sops_repo_server_values" not in _read(BOOTSTRAP)
