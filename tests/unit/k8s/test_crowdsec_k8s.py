"""CrowdSec on Kubernetes.

The shared preflight probes the engine with kubectl, the engine restart
rolls the local API deployment, and cscli runs through kubectl exec. At
deploy time k8s_reconcile_caddy.yml maps CANASTA_ENABLE_CROWDSEC and the
trusted-proxy mode into values.yaml, the chart gates the agent sidecar, the
local API deployment, its PVC, and its ConfigMap on crowdsec.enabled, and
k8s_sync_config.yml builds the crowdsec Secret and configData. Auto-enroll
waits for the local API rollout only on Kubernetes, and the Kubernetes
preflight's not-running message distinguishes an enabled engine.
"""

import os

import jinja2
import yaml

from test_crowdsec import HELM_DIR, REPO_ROOT, _read, _render_caddyfile


def _read_helm(name):
    return _read(os.path.join(HELM_DIR, "templates", name))


class TestCrowdsecEnrollRole:
    def test_preflight_supports_both_orchestrators(self):
        # CrowdSec runs on K8s too. The shared preflight must
        # no longer hard-refuse non-Compose, and must branch its engine-running
        # check: `docker compose ps` for Compose, a kubectl readiness probe of
        # the crowdsec sidecar for K8s.
        preflight = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_preflight.yml",
        ))
        assert "not in ['compose']" not in preflight, (
            "the Compose-only refusal must be gone — K8s is supported"
        )
        assert "{{ inspect_command | default('docker') }}" in preflight
        assert "kubectl get pods" in preflight
        assert "crowdsec=true" in preflight


class TestCrowdsecRestartEngine:
    def _content(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "crowdsec_restart_engine.yml",
        ))

    def test_k8s_rolls_the_local_api_deployment(self):
        # The K8s equivalent is a rollout of the local API deployment, waited
        # on so callers can exec into the fresh pod. Caddy is left alone.
        content = self._content()
        assert "kubectl rollout restart deployment/canasta-{{ instance_id }}-crowdsec" in content
        assert "kubectl rollout status deployment/canasta-{{ instance_id }}-crowdsec" in content


class TestCrowdsecResolveCscli:
    def _content(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "crowdsec_cscli_prefix.yml",
        ))

    def test_k8s_prefix_names_the_container(self):
        content = self._content()
        assert "kubectl exec" in content
        assert "-c {{ _cscli_k8s_container }} --" in content


class TestCrowdsecPreflightK8sReadiness:
    """The K8s local API readiness probe in _preflight.yml uses a jsonpath
    that contains a space (the 'name=ready ' separator). It must be quoted so
    Ansible's command parser keeps it as one argument — otherwise the trailing
    ' {end}' splits into its own token and kubectl reads it as a pod name,
    failing with 'name cannot be provided when a selector is specified'."""

    def _readiness_cmd(self):
        tasks = yaml.safe_load(_read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_preflight.yml")))
        found = []

        def walk(n):
            if isinstance(n, dict):
                cmd = n.get("ansible.builtin.command")
                if isinstance(cmd, dict) and "containerStatuses" in str(cmd.get("cmd", "")):
                    found.append(cmd["cmd"])
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(tasks)
        assert found, "expected the jsonpath readiness command in _preflight.yml"
        return found[0]

    def test_readiness_jsonpath_is_a_single_argument(self):
        import shlex
        tokens = shlex.split(self._readiness_cmd().replace("{{ instance_id }}", "big"))
        assert "{end}" not in tokens, (
            "jsonpath must be quoted — an unquoted ' {end}' splits into its own "
            "token and kubectl reads it as a pod name"
        )
        jp = [t for t in tokens if t.startswith("jsonpath=")]
        assert len(jp) == 1, f"expected exactly one jsonpath token, got {jp}"
        assert jp[0].endswith("{end}"), "the full jsonpath (incl. {end}) must be one token"


class TestCrowdsecK8sCaddyImageReconcile:
    """The K8s caddy image + crowdsec.enabled are reconciled into values.yaml
    from .env at deploy time by k8s_reconcile_caddy.yml — the analog of
    Compose's start-time sync_compose_profiles. A single reconcile point serves
    every entry path (config set restart, create -e, hand edit), so the deployed
    manifests always match the feature flags. Because it runs at deploy time
    (.env already written), it reads .env directly with no overlay."""

    def _reconcile(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "k8s_reconcile_caddy.yml"))

    def test_reconcile_reads_flags_and_picks_plugin_image(self):
        content = self._reconcile()
        assert "CANASTA_ENABLE_CROWDSEC" in content
        assert "TRUSTED_PROXIES" in content
        assert "cloudflare" in content and "imperva" in content
        assert "canasta_caddy_plugin_image" in content
        assert "caddy:2.10.2-alpine" in content

    def test_reconcile_sets_crowdsec_enabled_from_flag(self):
        content = self._reconcile()
        assert "'crowdsec': {'enabled':" in content, (
            "reconcile must set crowdsec.enabled in values.yaml from the flag"
        )

    def test_reconcile_only_touches_managed_images(self):
        # A custom operator image must be left untouched; managed = empty,
        # stock default, or any managed-prefix tag.
        content = self._reconcile()
        assert ".startswith(canasta_caddy_plugin_image" in content

    def test_reconcile_wired_into_start_and_create(self):
        start = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml"))
        create = _read(os.path.join(
            REPO_ROOT, "roles", "create", "tasks", "_start.yml"))
        assert "k8s_reconcile_caddy.yml" in start, (
            "the start path must reconcile before reading values.yaml"
        )
        assert "k8s_reconcile_caddy.yml" in create, (
            "create's K8s deploy bypasses start.yml, so it must reconcile too"
        )

    def test_side_effects_no_longer_reconciles_k8s_caddy(self):
        # Superseded by the start-path reconcile; leaving a second copy here
        # would re-introduce a create/config-set divergence.
        content = _read(os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml"))
        assert "Reconcile K8s caddy plugin image" not in content
        assert "_config_value if _config_key == 'CANASTA_ENABLE_CROWDSEC'" not in content


class TestCrowdsecK8sChart:
    def test_values_has_crowdsec_block(self):
        values = yaml.safe_load(_read(os.path.join(HELM_DIR, "values.yaml")))
        assert values["crowdsec"]["enabled"] is False, (
            "crowdsec must default off so existing deployments are unaffected"
        )
        assert "crowdsec" in values["configData"]

    def test_caddy_deployment_gates_agent_on_enabled(self):
        content = _read_helm("deployment-caddy.yaml")
        # Everything crowdsec is behind the enabled gate.
        assert "{{- if .Values.crowdsec.enabled }}" in content
        # Agent sidecar, log sharing, key env.
        assert "name: crowdsec-agent" in content
        assert "/var/log/caddy" in content
        assert "CROWDSEC_BOUNCER_API_KEY" in content
        assert "secretKeyRef" in content
        assert "optional: true" in content
        # No PVC left in the pod, so it rolls.
        assert "type: Recreate" not in content
        assert "persistentVolumeClaim" not in content

    def test_local_api_deployment_is_gated_and_holds_the_pvc(self):
        content = _read_helm("deployment-crowdsec.yaml")
        assert content.startswith("{{- if .Values.crowdsec.enabled }}")
        assert "seed-crowdsec-config" in content
        assert "claimName: {{ include \"canasta.fullname\" . }}-crowdsec" \
            in content
        assert "type: Recreate" in content

    def test_crowdsec_service_routes_lapi_to_crowdsec_pod(self):
        content = _read_helm("service-aliases.yaml")
        assert "name: crowdsec" in content
        assert "port: 8080" in content
        crowdsec_block = content[content.index("name: crowdsec"):]
        assert "app.kubernetes.io/component: crowdsec" in \
            crowdsec_block[crowdsec_block.index("selector:"):]

    def test_pvc_and_configmap_exist_and_are_gated(self):
        pvc = _read_helm("pvc-crowdsec.yaml")
        cm = _read_helm("configmap-crowdsec.yaml")
        assert "{{- if .Values.crowdsec.enabled }}" in pvc
        assert "{{- if .Values.crowdsec.enabled }}" in cm
        assert "/var/lib/crowdsec/data" in pvc or "subPath" in pvc.lower()


class TestCrowdsecK8sConfigSync:
    def _content(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_sync_config.yml",
        ))

    def test_creates_crowdsec_secret(self):
        content = self._content()
        assert "canasta-{{ instance_id }}-crowdsec" in content
        assert "CROWDSEC_BOUNCER_API_KEY" in content
        assert "CROWDSEC_AGENT_PASSWORD" in content
        # The Secret must be no_log so it never lands in output.
        secret_block = content[content.index("Build crowdsec Secret data"):]
        assert "no_log: true" in secret_block

    def test_syncs_acquisition_and_whitelist_into_configdata(self):
        content = self._content()
        assert "_crowdsec_config_data" in content
        assert "acquis.yaml" in content
        assert "whitelists.yaml" in content
        assert "'crowdsec': _crowdsec_config_data" in content


class TestCrowdsecK8sValuesPropagation:
    """crowdsec.enabled + the caddy plugin image reach the cluster via the
    deploy-time reconcile (k8s_reconcile_caddy.yml), not a config-set-time
    write in _side_effects (that path is superseded)."""

    def _content(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "k8s_reconcile_caddy.yml",
        ))

    def test_enable_flag_maps_to_chart_toggle(self):
        content = self._content()
        assert "'crowdsec': {'enabled':" in content

    def test_reconciles_caddy_plugin_image_on_k8s(self):
        content = self._content()
        # CrowdSec or a provider trusted-proxy mode swaps caddy.image to the
        # plugin image on K8s.
        assert "canasta_caddy_plugin_image" in content
        assert "TRUSTED_PROXIES" in content


class TestCrowdsecAutoEnroll:
    def _autoenroll(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "crowdsec_autoenroll.yml"))

    def test_autoenroll_local_api_wait_is_k8s_guarded(self):
        # The local API rollout wait is K8s-only; Compose starts the service
        # synchronously. The guard lives inside the orchestrator task (allowed
        # by the orchestrator-dispatch design), not in action code.
        content = self._autoenroll()
        assert "rollout status deployment/canasta-{{ instance_id }}-crowdsec" in content
        wait = content[content.index("rollout status"):]
        assert "when: instance_is_k8s | bool" in wait

    def test_autoenroll_is_self_contained(self):
        # The task derives its namespace from instance_id (a global fact in
        # both paths), so it must not depend on a caller-set _k8s_namespace.
        content = self._autoenroll()
        assert "-n canasta-{{ instance_id }}" in content
        assert "_k8s_namespace" not in content


class TestCrowdsecPreflightEnabledButNotRunning:
    def _fail_msgs(self):
        tasks = yaml.safe_load(_read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_preflight.yml")))
        return [
            t["ansible.builtin.fail"]["msg"]
            for b in tasks if "block" in b
            and b.get("when") == "instance_is_k8s | bool"
            for t in b["block"] if "ansible.builtin.fail" in t
        ]

    def _render(self, msg, enabled):
        env = jinja2.Environment()
        env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes")
        return env.from_string(msg).render(
            _crowdsec_enabled=enabled, instance_id="mysite")

    def test_k8s_message_distinguishes_enabled(self):
        msgs = self._fail_msgs()
        assert len(msgs) == 1
        enabled = self._render(msgs[0], True)
        disabled = self._render(msgs[0], False)
        assert "Enable it first" not in enabled
        assert "CrowdSec is enabled" in enabled
        assert "canasta status -i mysite" in enabled
        assert "Enable it first" in disabled


class TestCrowdsecK8sTrustedProxy:
    def test_rewrite_caddy_combines_cdn_ranges(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
        ))
        assert "_tp_combine" in content

    def test_k8s_no_cdn_renders_static_cluster_cidrs(self):
        out = _render_caddyfile(
            _crowdsec_active=True,
            _trusted_proxies_enabled=True,
            _tp_combine=False,
            _tp_dynamic=False,
            _tp_mode="",
            _trusted_proxies_headers="X-Forwarded-For",
            _trusted_proxies_strict=True,
            _trusted_proxies_cidrs=["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        )
        assert "trusted_proxies static 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16" in out
        assert "client_ip_headers X-Forwarded-For" in out
        assert "combine" not in out

    def test_k8s_cdn_renders_combine_of_static_and_cdn_ranges(self):
        out = _render_caddyfile(
            _crowdsec_active=True,
            _trusted_proxies_enabled=True,
            _tp_combine=True,
            _tp_dynamic=True,
            _tp_mode="cloudflare",
            _trusted_proxies_headers="Cf-Connecting-Ip",
            _trusted_proxies_strict=False,
            _trusted_proxies_cidrs=["10.0.0.0/8"],
        )
        assert "trusted_proxies combine {" in out
        assert "static 10.0.0.0/8" in out
        assert "cdn_ranges {" in out
        assert "provider cloudflare" in out
        assert "client_ip_headers Cf-Connecting-Ip" in out
