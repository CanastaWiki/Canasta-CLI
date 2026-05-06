"""Structural tests for Kubernetes/Helm files."""

import os

import pytest
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
HELM_CHART = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta"
)
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
ORCHESTRATOR_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


class TestHelmChart:
    def test_chart_yaml_exists(self):
        assert os.path.isfile(os.path.join(HELM_CHART, "Chart.yaml"))

    def test_values_yaml_exists(self):
        assert os.path.isfile(os.path.join(HELM_CHART, "values.yaml"))

    def test_chart_yaml_valid(self):
        with open(os.path.join(HELM_CHART, "Chart.yaml")) as f:
            chart = yaml.safe_load(f)
        assert chart["apiVersion"] == "v2"
        assert chart["name"] == "canasta"
        assert "version" in chart
        assert "appVersion" in chart

    def test_values_yaml_has_required_keys(self):
        with open(os.path.join(HELM_CHART, "values.yaml")) as f:
            values = yaml.safe_load(f)
        assert "instance" in values
        assert "image" in values
        assert "domains" in values
        assert "web" in values
        assert "db" in values
        assert "ingress" in values
        assert "persistence" in values
        assert "secrets" in values

    def test_required_templates_exist(self):
        templates = os.path.join(HELM_CHART, "templates")
        required = [
            "_helpers.tpl",
            "configmap-env.yaml",
            "deployment-caddy.yaml",
            "deployment-web.yaml",
            "deployment-varnish.yaml",
            "deployment-jobrunner.yaml",
            "statefulset-db.yaml",
            "statefulset-elasticsearch.yaml",
            "service-aliases.yaml",
            "ingress.yaml",
            "pvc-images.yaml",
            "secret-db.yaml",
            "argocd-application.yaml",
        ]
        for template in required:
            assert os.path.isfile(os.path.join(templates, template)), (
                "Missing template: %s" % template
            )

    def test_values_yaml_has_env_config_data(self):
        """configData.env must exist so configmap-env.yaml renders (#51)."""
        with open(os.path.join(HELM_CHART, "values.yaml")) as f:
            values = yaml.safe_load(f)
        assert "configData" in values
        assert "env" in values["configData"], (
            "configData.env is required for the env ConfigMap template"
        )

    def test_web_and_jobrunner_reference_env_configmap(self):
        """deployment-web and deployment-jobrunner must pull env vars from
        the canasta-<id>-env ConfigMap via envFrom, so that .env changes
        propagated by k8s_sync_config.yml actually reach the pod (#51)."""
        templates = os.path.join(HELM_CHART, "templates")
        for template_name in ("deployment-web.yaml", "deployment-jobrunner.yaml"):
            with open(os.path.join(templates, template_name)) as f:
                content = f.read()
            assert "envFrom:" in content, (
                "%s must reference the env ConfigMap via envFrom" % template_name
            )
            assert '{{ include "canasta.fullname" . }}-env' in content, (
                "%s must reference the canasta-<id>-env ConfigMap by name"
                % template_name
            )

    def test_persistence_subkeys_have_access_mode_default(self):
        """Each content PVC must have an accessMode default in values.yaml
        so the chart renders without requiring users to set it (#55)."""
        with open(os.path.join(HELM_CHART, "values.yaml")) as f:
            values = yaml.safe_load(f)
        assert "persistence" in values
        for subkey in ("images", "extensions", "skins", "publicAssets"):
            assert subkey in values["persistence"], (
                "persistence.%s missing from values.yaml" % subkey
            )
            assert "accessMode" in values["persistence"][subkey], (
                "persistence.%s.accessMode missing from values.yaml" % subkey
            )
            mode = values["persistence"][subkey]["accessMode"]
            assert mode in ("ReadWriteOnce", "ReadWriteMany"), (
                "persistence.%s.accessMode must be ReadWriteOnce or "
                "ReadWriteMany, got %r" % (subkey, mode)
            )

    def test_content_pvcs_use_configurable_access_mode(self):
        """The four content PVC templates must read accessMode from values.yaml
        rather than hardcoding ReadWriteOnce, so multi-node multi-replica
        web on RWM-capable storage is contractually correct (#55)."""
        templates = os.path.join(HELM_CHART, "templates")
        pvc_subkey_map = {
            "pvc-images.yaml": "images",
            "pvc-extensions.yaml": "extensions",
            "pvc-skins.yaml": "skins",
            "pvc-public-assets.yaml": "publicAssets",
        }
        for template_name, subkey in pvc_subkey_map.items():
            with open(os.path.join(templates, template_name)) as f:
                content = f.read()
            expected = ".Values.persistence.%s.accessMode" % subkey
            assert expected in content, (
                "%s must read accessMode from %s" % (template_name, expected)
            )
            # Sanity: the old hardcoded "- ReadWriteOnce" line should be gone.
            assert "- ReadWriteOnce" not in content, (
                "%s still has hardcoded '- ReadWriteOnce' — should be templated"
                % template_name
            )

    def test_k8s_preflight_uses_capacity_not_allocatable(self):
        """k8s_preflight.yml should read .status.capacity for the memory
        check, not .status.allocatable. Allocatable on a 4 GiB node is
        ~3.5 GiB, so a 4 GiB threshold against allocatable would reject
        the smallest instance we want to support. See #58."""
        with open(os.path.join(ORCHESTRATOR_TASKS, "k8s_preflight.yml")) as f:
            content = f.read()
        assert ".status.capacity.memory" in content, (
            "k8s_preflight.yml must read .status.capacity.memory"
        )
        assert ".status.allocatable.memory" not in content, (
            "k8s_preflight.yml should not read .status.allocatable.memory; "
            "use capacity instead. See #58."
        )

    def test_k8s_preflight_min_memory_is_3500(self):
        """The K8s preflight memory minimum should match the Compose
        threshold and accommodate cloud-instance kernel overhead (#65).
        4096 MiB rejects real 4 GiB cloud instances (which report
        ~3826 MiB capacity); 3500 is the working threshold."""
        with open(os.path.join(ORCHESTRATOR_TASKS, "k8s_preflight.yml")) as f:
            content = f.read()
        assert "_min_memory_mi: 3500" in content, (
            "k8s_preflight.yml _min_memory_mi must be 3500 (the cloud-"
            "overhead-aware floor for 4 GiB instances)"
        )
        assert "_min_memory_mi: 4096" not in content, (
            "k8s_preflight.yml still has the old 4096 MiB minimum; "
            "should be 3500. See #65."
        )
        assert "_min_memory_mi: 1216" not in content, (
            "k8s_preflight.yml still has the original 1216 MiB minimum; "
            "should be 3500. See #58 / #65."
        )


class TestExternalDatabase:
    """External DB support on the K8s path: chart gates db.enabled,
    surfaces externalDatabase, and the service alias for the internal
    db is only rendered when db.enabled=true."""

    def test_chart_values_has_external_database_defaults(self):
        with open(os.path.join(HELM_CHART, "values.yaml")) as f:
            values = yaml.safe_load(f)
        assert "externalDatabase" in values, (
            "values.yaml must expose externalDatabase for the external DB path"
        )
        assert values["db"]["enabled"] is True, (
            "Default db.enabled must be true (internal DB is the default)"
        )

    def test_statefulset_db_is_gated_on_db_enabled(self):
        path = os.path.join(HELM_CHART, "templates", "statefulset-db.yaml")
        with open(path) as f:
            content = f.read()
        assert "{{- if .Values.db.enabled }}" in content, (
            "statefulset-db.yaml must be gated on .Values.db.enabled"
        )

    def test_service_alias_db_is_gated_on_db_enabled(self):
        """The 'db' Service alias in service-aliases.yaml must only
        render when db.enabled=true. Otherwise we leave a dangling
        Service with no endpoints on external-DB deployments."""
        path = os.path.join(HELM_CHART, "templates", "service-aliases.yaml")
        with open(path) as f:
            content = f.read()
        # Find the section declaring the 'db' alias Service and verify
        # it sits inside a .Values.db.enabled guard.
        assert "name: db" in content, "service-aliases.yaml should declare a 'db' Service"
        db_idx = content.index("name: db")
        preceding = content[:db_idx]
        assert "{{- if .Values.db.enabled }}" in preceding.splitlines()[-10:][0:10].__str__() \
            or "{{- if .Values.db.enabled }}" in "\n".join(preceding.splitlines()[-5:]), (
            "The 'db' Service in service-aliases.yaml must be wrapped in "
            "a {{- if .Values.db.enabled }} guard"
        )

    def test_web_and_jobrunner_switch_mysql_host_on_db_enabled(self):
        """Both deployment templates must pick MYSQL_HOST from
        externalDatabase.host when db.enabled is false."""
        templates = os.path.join(HELM_CHART, "templates")
        for template_name in ("deployment-web.yaml", "deployment-jobrunner.yaml"):
            with open(os.path.join(templates, template_name)) as f:
                content = f.read()
            assert ".Values.externalDatabase.host" in content, (
                "%s must reference .Values.externalDatabase.host for "
                "external DB support" % template_name
            )
            assert ".Values.db.enabled" in content, (
                "%s must gate MYSQL_HOST on .Values.db.enabled" % template_name
            )

    def test_tls_cascade_on_localhost_to_domain_change(self):
        """When a user changes MW_SITE_SERVER from localhost to a
        real https:// domain on a K8s instance, _side_effects.yml
        should install cert-manager and patch values.yaml to enable
        ingress.tls — not force the user to delete and recreate."""
        path = os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml",
        )
        with open(path) as f:
            content = f.read()
        # Gates the cascade on K8s + non-localhost + https scheme
        assert "_new_scheme" in content, (
            "_side_effects.yml must capture the scheme from MW_SITE_SERVER"
        )
        assert "_new_fqdn != 'localhost'" in content, (
            "_side_effects.yml must not re-enable TLS on localhost changes"
        )
        # Installs cert-manager idempotently
        assert "k8s_certmanager.yml" in content, (
            "_side_effects.yml must install cert-manager on domain change"
        )
        # Patches values.yaml to enable ingress TLS
        assert "letsencrypt-prod" in content, (
            "_side_effects.yml must set the Let's Encrypt issuer"
        )

    def test_values_template_emits_external_db_when_flagged(self):
        """The Ansible k8s_values.yaml.j2 template must emit db.enabled
        and externalDatabase when _use_external_db is true."""
        template_path = os.path.join(
            REPO_ROOT, "roles", "orchestrator", "templates",
            "k8s_values.yaml.j2",
        )
        with open(template_path) as f:
            content = f.read()
        assert "_use_external_db" in content, (
            "k8s_values.yaml.j2 must consult the _use_external_db fact"
        )
        assert "externalDatabase:" in content, (
            "k8s_values.yaml.j2 must render an externalDatabase section"
        )
        assert "enabled: false" in content, (
            "k8s_values.yaml.j2 must flip db.enabled to false when external"
        )


class TestK8sSyncConfigSecretsStripped:
    """Guard the #507 regression class: secret-bearing keys must NOT
    end up duplicated in the configData.web['.env'] blob — the chart
    pulls MYSQL_PASSWORD / MW_SECRET_KEY from the canonical K8s
    Secrets via secretKeyRef, and any plaintext copy in the
    ConfigMap-backed .env file (a) duplicates the secret in the
    cluster and (b) forces the gitops repo to encrypt
    rendered-values.yaml, which Argo CD can't read."""

    SYNC_PATH = os.path.join(ORCHESTRATOR_TASKS, "k8s_sync_config.yml")

    def _load(self):
        with open(self.SYNC_PATH) as f:
            return yaml.safe_load(f)

    @staticmethod
    def _walk_tasks(tasks):
        for t in tasks or []:
            if not isinstance(t, dict):
                continue
            yield t
            for nested in ("block", "rescue", "always"):
                if nested in t:
                    yield from TestK8sSyncConfigSecretsStripped._walk_tasks(
                        t[nested],
                    )

    def test_sync_env_no_secrets_fact_is_set(self):
        """A set_fact must define _sync_env_no_secrets — the filtered
        version of .env that web ConfigMap embedding uses."""
        for task in self._walk_tasks(self._load()):
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if isinstance(sf, dict) and "_sync_env_no_secrets" in sf:
                return
        raise AssertionError(
            "k8s_sync_config.yml does not define _sync_env_no_secrets — "
            "without it, configData.web['.env'] would carry MYSQL_PASSWORD "
            "and MW_SECRET_KEY duplicated from the K8s Secrets, which "
            "forces git-crypt encryption of rendered-values.yaml and "
            "breaks Argo CD reconciliation (#507)."
        )

    def test_sync_env_no_secrets_rejects_password_and_secret_key(self):
        """The filter must specifically drop MYSQL_PASSWORD and
        MW_SECRET_KEY — those are the keys the chart pulls via
        secretKeyRef and therefore the keys that would be duplicated
        if left in the .env content."""
        for task in self._walk_tasks(self._load()):
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if not (isinstance(sf, dict) and "_sync_env_no_secrets" in sf):
                continue
            expr = sf["_sync_env_no_secrets"]
            assert "MYSQL_PASSWORD" in expr, (
                "_sync_env_no_secrets filter must drop MYSQL_PASSWORD"
            )
            assert "MW_SECRET_KEY" in expr, (
                "_sync_env_no_secrets filter must drop MW_SECRET_KEY"
            )
            return

    def test_web_config_data_uses_filtered_env(self):
        """configData.web['.env'] must come from the filtered
        _sync_env_no_secrets, not from the raw _sync_env."""
        for task in self._walk_tasks(self._load()):
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if not (isinstance(sf, dict) and "_web_config_data" in sf):
                continue
            expr = sf["_web_config_data"]
            assert "_sync_env_no_secrets" in expr, (
                "_web_config_data must reference _sync_env_no_secrets — "
                "raw _sync_env carries MYSQL_PASSWORD/MW_SECRET_KEY (#507)."
            )
            assert "_sync_env.content" not in expr, (
                "_web_config_data must NOT reference _sync_env.content "
                "directly — that's the unfiltered .env containing secrets "
                "the chart already pulls via secretKeyRef (#507)."
            )
            return
        raise AssertionError(
            "k8s_sync_config.yml has no _web_config_data set_fact"
        )


class TestK8sGitopsNoEncryption:
    """Guard the #507 regression class for the gitops side: K8s init
    must not register hosts/** for git-crypt filtering. After the
    fix, rendered-values.yaml carries no secrets so encryption is
    unnecessary; the encryption rule was what broke Argo CD's ability
    to parse the values file."""

    INIT_K8S = os.path.join(GITOPS_TASKS, "init_kubernetes.yml")

    @staticmethod
    def _walk_tasks(tasks):
        for t in tasks or []:
            if not isinstance(t, dict):
                continue
            yield t
            for nested in ("block", "rescue", "always"):
                if nested in t:
                    yield from TestK8sGitopsNoEncryption._walk_tasks(
                        t[nested],
                    )

    def test_gitattributes_does_not_filter_hosts(self):
        """The .gitattributes that K8s gitops init writes must not
        register `hosts/**` (or any pattern) for git-crypt filtering.
        Argo CD reads hosts/<name>/rendered-values.yaml as Helm values
        and has no git-crypt awareness — encrypted bytes break parsing
        with a 'control characters not allowed' error (#507).

        After #509 dropped the entire git-crypt scaffolding from K8s,
        there's no .gitattributes-writing task at all, which trivially
        satisfies the 'no encryption rules' invariant. This test
        accepts either shape."""
        with open(self.INIT_K8S) as f:
            tasks = yaml.safe_load(f)
        for task in self._walk_tasks(tasks):
            copy = task.get("ansible.builtin.copy") or task.get("copy")
            if not isinstance(copy, dict):
                continue
            dest = copy.get("dest", "")
            if not dest.endswith("/.gitattributes"):
                continue
            content = copy.get("content")
            src = copy.get("src", "")
            if content is not None:
                assert "filter=git-crypt" not in content, (
                    "K8s gitops init writes a .gitattributes that "
                    "registers files for git-crypt encryption. After "
                    "#507, rendered-values.yaml is cleartext and Argo "
                    "CD must be able to parse it — no encryption rules."
                )
                return
            assert "gitattributes.default" not in src, (
                "K8s gitops init still copies the canonical "
                "gitattributes.default (which encrypts hosts/**). "
                "After #507 the K8s path has no secrets in repo, so it "
                "must write its own .gitattributes without the "
                "encryption rule."
            )
            return
        # No .gitattributes-writing task at all (#509 result) is fine —
        # the absence of a filter rule is the same as an empty rule
        # set as far as Argo CD is concerned.


class TestK8sGitopsNoGitcryptScaffolding:
    """Guard #509: K8s gitops init/join must not invoke any git-crypt
    operations. After #508 nothing in the K8s repo is encrypted, so
    `git-crypt --version` checks, `git-crypt init`, `git-crypt
    export-key`, `git-crypt unlock`, and the controller-side write of
    the exported key are all dead scaffolding. They were removed in
    #509 to simplify the K8s gitops flow and to drop git-crypt as a
    K8s prerequisite (Compose still uses it)."""

    INIT_K8S = os.path.join(GITOPS_TASKS, "init_kubernetes.yml")
    JOIN_K8S = os.path.join(GITOPS_TASKS, "join_kubernetes.yml")

    def _active_content(self, path):
        """Concat non-comment lines so explanatory comments mentioning
        git-crypt don't false-positive against the regression check."""
        with open(path) as f:
            return "\n".join(
                line for line in f.read().splitlines()
                if not line.lstrip().startswith("#")
            )

    def test_init_does_not_check_gitcrypt_installed(self):
        c = self._active_content(self.INIT_K8S)
        assert "git-crypt --version" not in c, (
            "init_kubernetes.yml has a `git-crypt --version` "
            "preflight check; that's dead scaffolding after #509 — "
            "K8s gitops doesn't use git-crypt."
        )

    def test_init_does_not_run_gitcrypt_init(self):
        c = self._active_content(self.INIT_K8S)
        assert "git-crypt init" not in c, (
            "init_kubernetes.yml runs `git-crypt init` but that's a "
            "no-op now (no .gitattributes filter rule). Removed in "
            "#509."
        )

    def test_init_does_not_export_gitcrypt_key(self):
        c = self._active_content(self.INIT_K8S)
        assert "git-crypt export-key" not in c, (
            "init_kubernetes.yml exports a git-crypt key; with no "
            "encryption applied, the key is dead bytes (#509)."
        )

    def test_join_does_not_check_gitcrypt_installed(self):
        c = self._active_content(self.JOIN_K8S)
        assert "git-crypt --version" not in c, (
            "join_kubernetes.yml has a `git-crypt --version` check; "
            "K8s gitops repos are cleartext after #509, no git-crypt "
            "needed."
        )

    def test_join_does_not_unlock_gitcrypt(self):
        c = self._active_content(self.JOIN_K8S)
        assert "git-crypt unlock" not in c, (
            "join_kubernetes.yml runs `git-crypt unlock`; nothing in "
            "the K8s gitops repo is encrypted (#509). Compose still "
            "uses unlock — that's in roles/gitops/tasks/join.yml."
        )

    def test_compose_join_still_uses_gitcrypt(self):
        """Belt-and-suspenders: Compose's gitops join MUST still
        unlock git-crypt — its repo encrypts hosts/<host>/vars.yaml.
        Removing git-crypt from Compose would be a regression."""
        compose_join = os.path.join(GITOPS_TASKS, "join.yml")
        with open(compose_join) as f:
            content = f.read()
        assert "git-crypt unlock" in content, (
            "join.yml (Compose) must still use git-crypt unlock — "
            "Compose's repo encrypts hosts/<host>/vars.yaml. #509 "
            "was K8s-only."
        )


class TestK8sStartReadsValuesFromTarget:
    """Guard #514: lookup('file', ...) runs on the controller, but
    instance_path is target-side. canasta start broke against any
    remote K8s target because three set_fact tasks tried to read the
    instance's values.yaml via lookup. Ban the anti-pattern here so
    the same regression can't drift back in (PR #115 had to fix the
    same shape in init_kubernetes.yml + push_kubernetes.yml; #265 and
    earlier reintroduced it in start.yml)."""

    START_YML = os.path.join(ORCHESTRATOR_TASKS, "start.yml")

    def test_no_lookup_file_against_instance_path(self):
        """No `lookup('file', instance_path ~ ...)` calls. They MUST
        be slurp-based to read on the play target via SSH."""
        with open(self.START_YML) as f:
            content = f.read()
        # Strip comments before checking — explanatory comments may
        # mention the old pattern legitimately.
        non_comment_lines = [
            line for line in content.splitlines()
            if not line.strip().startswith("#")
        ]
        active = "\n".join(non_comment_lines)
        assert "lookup('file', instance_path" not in active, (
            "start.yml has a lookup('file', instance_path ~ ...) call. "
            "lookup runs on the controller but instance_path is the "
            "target-side path; this breaks canasta start against any "
            "remote K8s host. Use ansible.builtin.slurp + from_yaml. "
            "See #514."
        )

    def test_values_yaml_slurped(self):
        """The K8s start path must slurp values.yaml from the target
        and parse it into a single fact (_start_values) so all three
        downstream reads (web.replicaCount, db.enabled,
        argocd.syncPolicy) share one source."""
        with open(self.START_YML) as f:
            content = f.read()
        assert "ansible.builtin.slurp:" in content, (
            "start.yml must slurp values.yaml from the target instead "
            "of looking it up controller-side (#514)"
        )
        assert "_start_values" in content, (
            "start.yml should parse the slurped values into a "
            "_start_values fact and reuse it across the three reads"
        )


class TestGitopsDispatchers:
    """Verify that each dispatched gitops command has both variants."""

    DISPATCHED = ["init", "push", "pull", "diff"]

    def test_dispatcher_files_exist(self):
        for cmd in self.DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s.yml" % cmd)
            assert os.path.isfile(path), "Missing dispatcher: %s.yml" % cmd

    def test_compose_variants_exist(self):
        for cmd in self.DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s_compose.yml" % cmd)
            assert os.path.isfile(path), (
                "Missing compose variant: %s_compose.yml" % cmd
            )

    def test_kubernetes_variants_exist(self):
        for cmd in self.DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s_kubernetes.yml" % cmd)
            assert os.path.isfile(path), (
                "Missing kubernetes variant: %s_kubernetes.yml" % cmd
            )

    def test_dispatchers_include_resolve_instance(self):
        for cmd in self.DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s.yml" % cmd)
            with open(path) as f:
                content = f.read()
            assert "resolve_instance" in content, (
                "Dispatcher %s.yml missing resolve_instance" % cmd
            )

    def test_compose_variants_do_not_resolve_instance(self):
        for cmd in self.DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s_compose.yml" % cmd)
            with open(path) as f:
                content = f.read()
            assert "resolve_instance" not in content, (
                "Compose variant %s_compose.yml should not resolve_instance "
                "(dispatcher handles it)" % cmd
            )


class TestOrchestratorTasks:
    """Verify Kubernetes orchestrator tasks exist."""

    REQUIRED_TASKS = [
        "helm_deploy.yml",
        "helm_uninstall.yml",
        "helm_status.yml",
        "k8s_preflight.yml",
        "k8s_install_k3s.yml",
        "k8s_argocd_bootstrap.yml",
        "k8s_apply_secrets.yml",
        "k8s_argocd_sync.yml",
        "k8s_get_pod.yml",
        "k8s_sync_config.yml",
    ]

    def test_k8s_tasks_exist(self):
        for task in self.REQUIRED_TASKS:
            path = os.path.join(ORCHESTRATOR_TASKS, task)
            assert os.path.isfile(path), "Missing task: %s" % task

    def test_start_has_kubernetes_block(self):
        with open(os.path.join(ORCHESTRATOR_TASKS, "start.yml")) as f:
            content = f.read()
        assert "Scale up" in content or "kubernetes" in content

    def test_stop_has_kubernetes_block(self):
        with open(os.path.join(ORCHESTRATOR_TASKS, "stop.yml")) as f:
            content = f.read()
        assert "Scale down" in content

    def test_destroy_has_kubernetes_block(self):
        with open(os.path.join(ORCHESTRATOR_TASKS, "destroy.yml")) as f:
            content = f.read()
        assert "helm_uninstall" in content


class TestGitopsComposeGitEnv:
    """Every git command in the Compose-side gitops flows must run with
    `environment: "{{ gitops_git_env }}"` so the operator gets:

    - ssh-agent forwarding (via ANSIBLE_SSH_ARGS' ForwardAgent=yes,
      tested separately) chained with this env's GIT_SSH_COMMAND, so
      `git push` on the remote authenticates against private forges.
    - StrictHostKeyChecking=accept-new + UserKnownHostsFile=… so a
      first contact with github.com etc. doesn't kill the play.

    Drift here is invisible at runtime until someone tries to
    onboard a private repo, so the structural check is worth its
    weight in cheap-and-mechanical lines.

    The Kubernetes-side flows generate their own deploy key and
    set GIT_SSH_COMMAND with `-i <key>`; they're allowed to use a
    different env (or no env reference at all).
    """

    # Files on the Compose path that contain at least one git command.
    COMPOSE_FILES = [
        "init_compose.yml",
        "pull_compose.yml",
        "push_compose.yml",
        "join.yml",  # Compose join (k8s join is in join_kubernetes.yml)
    ]

    GIT_VERBS = (
        "git push",
        "git pull",
        "git clone",
        "git fetch",
        "git ls-remote",
    )

    @pytest.mark.parametrize("filename", COMPOSE_FILES)
    def test_every_git_command_has_gitops_git_env(self, filename):
        path = os.path.join(GITOPS_TASKS, filename)
        with open(path) as f:
            tasks = yaml.safe_load(f) or []
        # Recursive walk: gitops_*_compose files nest tasks inside
        # block: structures, so we descend into block/rescue/always
        # rather than just iterating the top level.
        offending = []
        for entry in self._walk_tasks(tasks):
            cmd = self._extract_cmd(entry)
            if not cmd:
                continue
            if not any(verb in cmd for verb in self.GIT_VERBS):
                continue
            env = entry.get("environment")
            ok = (
                isinstance(env, str)
                and "gitops_git_env" in env
            ) or (
                isinstance(env, dict)
                and any(
                    "GIT_SSH_COMMAND" in str(v) for v in env.values()
                )
                # The k8s-style override (-i <_ssh_key_path>) doesn't
                # apply on the Compose side; require gitops_git_env.
                and "gitops_git_env" in str(env)
            )
            if not ok:
                offending.append(
                    "%s: task '%s' ran '%s' without gitops_git_env"
                    % (filename, entry.get("name", "<unnamed>"), cmd)
                )
        assert not offending, "\n".join(offending)

    @classmethod
    def _walk_tasks(cls, items):
        """Yield every task dict regardless of nesting depth."""
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            yield item
            for child_key in ("block", "rescue", "always"):
                if child_key in item:
                    yield from cls._walk_tasks(item[child_key])

    @staticmethod
    def _extract_cmd(task):
        """Pull the cmd string out of an ansible.builtin.command task."""
        for key in ("ansible.builtin.command", "command"):
            mod = task.get(key)
            if isinstance(mod, dict):
                cmd = mod.get("cmd")
                return cmd if isinstance(cmd, str) else ""
            if isinstance(mod, str):
                return mod
        return ""


class TestGitopsReinit:
    """Every init/join entry point must include _reinit_cleanup.yml
    gated on the reinit flag, and surface --reinit in the
    "already initialized" failure message. Drift is silent: the user
    gets stuck with a partial init and no way out, which is exactly
    the failure mode #462 reported. Cheap to gate against."""

    INIT_JOIN_FILES = [
        "init_compose.yml",
        "init_kubernetes.yml",
        "join.yml",
        "join_kubernetes.yml",
    ]

    @pytest.mark.parametrize("filename", INIT_JOIN_FILES)
    def test_includes_reinit_cleanup(self, filename):
        with open(os.path.join(GITOPS_TASKS, filename)) as f:
            content = f.read()
        assert "_reinit_cleanup.yml" in content, (
            "%s should include _reinit_cleanup.yml so --reinit can "
            "wipe partial gitops state" % filename
        )
        assert "reinit | default(false) | bool" in content, (
            "%s should gate the cleanup on the reinit flag" % filename
        )

    @pytest.mark.parametrize("filename", INIT_JOIN_FILES)
    def test_already_initialized_message_mentions_reinit(self, filename):
        with open(os.path.join(GITOPS_TASKS, filename)) as f:
            content = f.read()
        assert "already initialized" in content
        # The error has to point users at the recovery flag, otherwise
        # the message is the same brick wall #462 was reported about.
        assert "--reinit" in content, (
            "%s's 'already initialized' message must mention --reinit "
            "so users have a documented recovery path" % filename
        )

    def test_cleanup_file_exists(self):
        assert os.path.isfile(
            os.path.join(GITOPS_TASKS, "_reinit_cleanup.yml")
        )


class TestResolvePasswordHelper:
    """The audit asked for `_resolve_password.yml` to dedupe the
    'use-provided-or-generate' pattern that was repeated three times
    in roles/create/tasks/main.yml. These tests guard against:

    1. The helper getting deleted or moved without updating the call
       sites.
    2. Future password-resolution code in main.yml drifting away from
       the helper and bringing the duplication back.
    """

    HELPER_PATH = os.path.join(
        os.path.dirname(__file__), "..", "..",
        "roles", "create", "tasks", "_resolve_password.yml",
    )
    CREATE_TASKS_DIR = os.path.join(
        os.path.dirname(__file__), "..", "..",
        "roles", "create", "tasks",
    )

    def _read_create_tasks(self):
        """Combined contents of all create-role task files (excluding
        the helper itself). The three password resolutions used to
        live in main.yml; after the #428 split they live in
        _passwords.yml. The structural assertions don't care which
        file holds them — they just guard against the helper pattern
        being abandoned."""
        combined = []
        for fname in sorted(os.listdir(self.CREATE_TASKS_DIR)):
            if not fname.endswith(".yml"):
                continue
            if fname == "_resolve_password.yml":
                continue  # helper itself; calls generate_password.yml
            with open(os.path.join(self.CREATE_TASKS_DIR, fname)) as f:
                combined.append(f.read())
        return "\n".join(combined)

    def test_helper_exists(self):
        assert os.path.isfile(self.HELPER_PATH)

    def test_helper_sets_resolved_password(self):
        with open(self.HELPER_PATH) as f:
            tasks = yaml.safe_load(f)
        # Walk every task (including those in `block:`) and make sure
        # at least one set_fact targets `_resolved_password` — the
        # contract the call sites depend on.
        def walk(items):
            for t in items or []:
                if not isinstance(t, dict):
                    continue
                yield t
                for k in ("block", "rescue", "always"):
                    if k in t:
                        yield from walk(t[k])
        sets_resolved = []
        for t in walk(tasks):
            sf = t.get("ansible.builtin.set_fact") or t.get("set_fact")
            if isinstance(sf, dict) and "_resolved_password" in sf:
                sets_resolved.append(t.get("name", "<unnamed>"))
        assert sets_resolved, (
            "_resolve_password.yml must set _resolved_password "
            "(both the use-provided and generate branches). Found: %r"
            % sets_resolved
        )

    def test_main_uses_helper_for_each_password(self):
        content = self._read_create_tasks()
        # Three password resolutions: root DB, wiki DB (non-root user
        # branch), admin. Each must include the helper.
        helper_includes = content.count("include_tasks: _resolve_password.yml")
        assert helper_includes >= 3, (
            "Expected at least 3 includes of _resolve_password.yml "
            "(rootdbpass, wikidbpass non-root branch, admin) across "
            "roles/create/tasks/*.yml; found %d" % helper_includes
        )

    def test_main_no_inline_generate_password_for_create_passwords(self):
        """Defensive: nobody should inline-call generate_password.yml
        from the create role for the three passwords the helper now
        owns. If someone adds a new password type that needs the same
        pattern, they should call the helper instead of copy-pasting
        the loop."""
        content = self._read_create_tasks()
        # The helper itself includes generate_password.yml; create-
        # role task files should not directly include it.
        assert "include_tasks:" in content
        inline_generate = content.count("generate_password.yml")
        # 0 is the win; tolerate if some other site legitimately
        # includes it without the helper.
        assert inline_generate <= 0, (
            "Found %d inline generate_password.yml include(s) in "
            "roles/create/tasks/*.yml — these should go through "
            "_resolve_password.yml." % inline_generate
        )


class TestResolveInstanceSkipGate:
    """`instance_lifecycle/start.yml` and `restart.yml` are entered
    both top-level (`canasta start` / `canasta restart`) and from
    nested callers (config set/unset, add, backup restore, devmode
    enable/disable, upgrade) that already passed through
    resolve_instance.yml. The include must be gated on
    `instance_path is not defined` so nested callers skip the
    redundant registry round-trip."""

    LIFECYCLE_FILES = ["start.yml", "restart.yml"]

    @pytest.mark.parametrize("filename", LIFECYCLE_FILES)
    def test_resolve_instance_gated_on_instance_path(self, filename):
        path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "roles", "instance_lifecycle", "tasks", filename,
        )
        with open(path) as f:
            tasks = yaml.safe_load(f)
        for t in tasks:
            if not isinstance(t, dict):
                continue
            inc = t.get("ansible.builtin.include_tasks") or t.get("include_tasks", "")
            if "resolve_instance.yml" not in str(inc):
                continue
            when = t.get("when", "")
            assert "instance_path is not defined" in when, (
                "%s: resolve_instance.yml include must be gated on "
                "`instance_path is not defined`; got when=%r"
                % (filename, when)
            )
            return
        # If we never found a resolve_instance include, the file
        # changed shape — flag it so reviewers don't lose the test.
        raise AssertionError(
            "%s no longer includes resolve_instance.yml — the gate "
            "test needs updating to reflect the new flow." % filename
        )


class TestPlayLevelDockerHost:
    """canasta.yml's play-level `environment:` exposes DOCKER_HOST so
    every Ansible-routed command's docker / docker compose calls
    inherit it. Operators on rootless podman or rootless Docker
    set this via `canasta create --docker-host=…` (see #479).

    Drift here is invisible until someone tries the rootless setup,
    so the structural check is cheap insurance."""

    CANASTA_YML = os.path.join(
        os.path.dirname(__file__), "..", "..", "canasta.yml",
    )

    def test_play_environment_exports_docker_host(self):
        with open(self.CANASTA_YML) as f:
            plays = yaml.safe_load(f)
        # The file has one play.
        play = plays[0]
        env = play.get("environment", {})
        assert "DOCKER_HOST" in env, (
            "canasta.yml must declare a play-level "
            "`environment: { DOCKER_HOST: … }` so docker tasks "
            "honor --docker-host / registry dockerHost."
        )

    def test_docker_host_precedence_flag_then_registry(self):
        # The expression has to:
        #   - prefer the `docker_host` var (from --docker-host on the
        #     current invocation) over the registry value
        #   - fall back to `instance_docker_host` (set by
        #     resolve_instance.yml from the registry) when the flag
        #     wasn't passed
        #   - omit the env var entirely when neither is set, so
        #     Docker keeps using its compiled-in default socket
        with open(self.CANASTA_YML) as f:
            plays = yaml.safe_load(f)
        expr = plays[0]["environment"]["DOCKER_HOST"]
        assert "docker_host" in expr
        assert "instance_docker_host" in expr
        assert "omit" in expr
