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
