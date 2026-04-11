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


class TestGitopsDispatchers:
    """Verify that each dispatched gitops command has both variants."""

    DISPATCHED = ["init", "push", "pull", "status", "diff"]

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
