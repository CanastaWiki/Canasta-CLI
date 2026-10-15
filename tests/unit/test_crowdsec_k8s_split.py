"""On Kubernetes the CrowdSec local API runs apart from the Caddy pod.

The local API holds the ReadWriteOnce PVC in its own Deployment, and an
agent sidecar in the caddy pod parses the access log and reports to it, so
the caddy Deployment rolls instead of recreating: a Caddy change no longer
takes the wiki down. The agent logs in with a password the CLI generates
once and delivers through the crowdsec Secret.
"""

import os
import shutil
import signal
import subprocess
import sys
import time

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
sys.path.insert(0, os.path.dirname(__file__))

from canasta_gitops import canasta_secret_checksums  # noqa: E402
from test_gitops_sops_secrets_from_local import (  # noqa: E402
    _play, _sync_instance, _sync_tasks)

CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")
AGENT_WRAPPER = os.path.join(CHART, "files", "crowdsec-agent-start.sh")
ORCH = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


def _render(*args):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    cmd = ["helm", "template", "canasta-demo", CHART,
           "--set", "instance.id=demo", "--set", "domains[0]=wiki.example.com",
           *args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _find(docs, kind, name):
    return next((d for d in docs if d["kind"] == kind
                 and d["metadata"]["name"] == name), None)


def _container(pod_spec, name):
    return next(c for c in pod_spec["containers"] if c["name"] == name)


def _env(container):
    return {e["name"]: e for e in container.get("env", [])}


ON = ("--set", "crowdsec.enabled=true")


class TestChartLayout:
    def test_caddy_rolls_without_downtime(self):
        for args in [(), ON]:
            caddy = _find(_render(*args), "Deployment", "canasta-demo-caddy")
            assert caddy["spec"]["strategy"] == {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}

    def test_caddy_pod_holds_no_volume_claim(self):
        caddy = _find(_render(*ON), "Deployment", "canasta-demo-caddy")
        pod = caddy["spec"]["template"]["spec"]
        assert not [v for v in pod["volumes"] if "persistentVolumeClaim" in v]
        assert "initContainers" not in pod

    def test_agent_sidecar_reads_the_log_and_reports_to_the_service(self):
        caddy = _find(_render(*ON), "Deployment", "canasta-demo-caddy")
        pod = caddy["spec"]["template"]["spec"]
        agent = _container(pod, "crowdsec-agent")
        env = _env(agent)
        assert env["DISABLE_LOCAL_API"]["value"] == "true"
        assert env["LOCAL_API_URL"]["value"] == "http://crowdsec:8080"
        assert env["AGENT_USERNAME"]["value"] == "canasta-caddy-agent"
        ref = env["AGENT_PASSWORD"]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "canasta-demo-crowdsec"
        assert ref["key"] == "CROWDSEC_AGENT_PASSWORD"
        # A missing password stalls the rollout with the old pod serving,
        # instead of an agent that silently cannot log in.
        assert not ref.get("optional", False)
        mounts = {m["mountPath"]: m for m in agent["volumeMounts"]}
        assert mounts["/var/log/caddy"]["name"] == "caddy-logs"
        assert mounts["/var/log/caddy"]["readOnly"] is True
        assert "/cm" in mounts
        caddy_mounts = {m["mountPath"]
                        for m in _container(pod, "caddy")["volumeMounts"]}
        assert "/var/log/caddy" in caddy_mounts

    def test_agent_never_gates_caddy_readiness(self):
        caddy = _find(_render(*ON), "Deployment", "canasta-demo-caddy")
        agent = _container(caddy["spec"]["template"]["spec"], "crowdsec-agent")
        assert "readinessProbe" not in agent
        assert "livenessProbe" not in agent

    def test_local_api_takes_the_existing_claim(self):
        docs = _render(*ON)
        lapi = _find(docs, "Deployment", "canasta-demo-crowdsec")
        assert lapi["spec"]["strategy"] == {"type": "Recreate"}
        assert lapi["metadata"]["annotations"][
            "meta.helm.sh/release-name"] == "canasta-demo"
        pod = lapi["spec"]["template"]["spec"]
        claim = next(v for v in pod["volumes"] if "persistentVolumeClaim" in v)
        assert claim["persistentVolumeClaim"]["claimName"] \
            == "canasta-demo-crowdsec"
        assert _find(docs, "PersistentVolumeClaim", "canasta-demo-crowdsec")
        engine = _container(pod, "crowdsec")
        subpaths = {m["mountPath"]: m.get("subPath")
                    for m in engine["volumeMounts"]}
        assert subpaths == {"/etc/crowdsec": "etc",
                            "/var/lib/crowdsec/data": "data"}
        env = _env(engine)
        assert env["DISABLE_AGENT"]["value"] == "true"
        assert env["AGENT_USERNAME"]["value"] == "canasta-caddy-agent"
        assert env["AGENT_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] \
            == "CROWDSEC_AGENT_PASSWORD"
        assert "local_only" in engine["command"][2]

    def test_service_routes_to_the_local_api_pod(self):
        docs = _render(*ON)
        svc = _find(docs, "Service", "crowdsec")
        lapi = _find(docs, "Deployment", "canasta-demo-crowdsec")
        labels = lapi["spec"]["template"]["metadata"]["labels"]
        assert svc["spec"]["selector"]["app.kubernetes.io/component"] \
            == "crowdsec"
        assert all(labels.get(k) == v
                   for k, v in svc["spec"]["selector"].items())
        assert svc["spec"]["ports"][0]["port"] == 8080

    def test_nothing_crowdsec_renders_when_disabled(self):
        docs = _render()
        assert _find(docs, "Deployment", "canasta-demo-crowdsec") is None
        caddy = _find(docs, "Deployment", "canasta-demo-caddy")
        names = [c["name"] for c in caddy["spec"]["template"]["spec"]
                 ["containers"]]
        assert names == ["caddy"]

    def test_local_api_rolls_when_the_agent_password_changes(self):
        lapi = _find(_render(*ON, "--set", "secretChecksums.crowdsec=abc"),
                     "Deployment", "canasta-demo-crowdsec")
        assert lapi["spec"]["template"]["metadata"]["annotations"] == {
            "checksum/secrets": "abc"}


class TestSecretChecksums:
    def _secret(self, **data):
        import base64
        return {"metadata": {"name": "canasta-demo-crowdsec"},
                "data": {k: base64.b64encode(v.encode()).decode()
                         for k, v in data.items()}}

    def test_agent_password_rolls_both_pods(self):
        before = canasta_secret_checksums(
            [self._secret(CROWDSEC_AGENT_PASSWORD="a")], {}, "demo")
        after = canasta_secret_checksums(
            [self._secret(CROWDSEC_AGENT_PASSWORD="b")], {}, "demo")
        assert before["crowdsec"] != after["crowdsec"]
        assert before["caddy"] != after["caddy"]
        assert before["web"] == after["web"]

    def test_bouncer_key_rolls_only_caddy(self):
        before = canasta_secret_checksums(
            [self._secret(CROWDSEC_BOUNCER_API_KEY="a")], {}, "demo")
        after = canasta_secret_checksums(
            [self._secret(CROWDSEC_BOUNCER_API_KEY="b")], {}, "demo")
        assert before["caddy"] != after["caddy"]
        assert before["crowdsec"] == after["crowdsec"]


class TestAgentPassword:
    def _sync(self, tmp_path, env_lines):
        inst = _sync_instance(tmp_path, env_lines, None)
        tasks = _sync_tasks() + [{
            "ansible.builtin.copy": {
                "content": "{{ _k8s_local_secrets | to_json }}",
                "dest": str(tmp_path / "local.json"), "mode": "0600"}}]
        rc, out = _play(tmp_path, tasks, {"instance_path": str(inst),
                                          "instance_id": "demo"})
        assert rc == 0, out
        env = dict(line.split("=", 1) for line in
                   (inst / ".env").read_text().splitlines() if "=" in line)
        return env, yaml.safe_load((tmp_path / "local.json").read_text())

    def test_generated_once_and_put_in_the_secret(self, tmp_path):
        env, local = self._sync(tmp_path, ["CANASTA_ENABLE_CROWDSEC=true"])
        password = env["CROWDSEC_AGENT_PASSWORD"]
        assert len(password) == 30
        assert local["canasta-demo-crowdsec"] == {
            "CROWDSEC_AGENT_PASSWORD": password}

    def test_an_existing_password_is_kept(self, tmp_path):
        env, local = self._sync(tmp_path, [
            "CANASTA_ENABLE_CROWDSEC=true",
            "CROWDSEC_AGENT_PASSWORD=kept",
            "CROWDSEC_BOUNCER_API_KEY=bouncer"])
        assert env["CROWDSEC_AGENT_PASSWORD"] == "kept"
        assert local["canasta-demo-crowdsec"] == {
            "CROWDSEC_AGENT_PASSWORD": "kept",
            "CROWDSEC_BOUNCER_API_KEY": "bouncer"}

    def test_not_generated_without_crowdsec(self, tmp_path):
        env, local = self._sync(tmp_path, ["MW_SITE_FQDN=example.com"])
        assert "CROWDSEC_AGENT_PASSWORD" not in env
        assert "canasta-demo-crowdsec" not in local

    def test_kept_out_of_the_web_configmap(self, tmp_path):
        inst = _sync_instance(tmp_path, ["CANASTA_ENABLE_CROWDSEC=true"], None)
        rc, out = _play(tmp_path, _sync_tasks(), {
            "instance_path": str(inst), "instance_id": "demo"})
        assert rc == 0, out
        data = yaml.safe_load((inst / "values-configdata.yaml").read_text())
        assert "CROWDSEC_AGENT_PASSWORD" not in data["configData"]["web"][".env"]


class TestAgentWrapper:
    """The wrapper keeps the agent container running whatever the agent
    does, so the caddy pod stays Ready."""

    def _wrapper(self, tmp_path):
        etc = tmp_path / "etc"
        cm = tmp_path / "cm"
        cm.mkdir()
        (cm / "acquis.yaml").write_text("acquis\n")
        (cm / "whitelists.yaml").write_text("whitelist\n")
        start = tmp_path / "docker_start.sh"
        start.write_text('echo run >> "%s"\nexit 1\n' % (tmp_path / "runs"))
        with open(AGENT_WRAPPER) as f:
            script = f.read()
        script = (script.replace("/docker_start.sh", str(start))
                  .replace("/etc/crowdsec", str(etc))
                  .replace("/cm/", str(cm) + "/")
                  .replace("delay=5", "delay=0"))
        return script, etc

    def test_restarts_a_failing_agent_and_stops_cleanly(self, tmp_path):
        script, etc = self._wrapper(tmp_path)
        proc = subprocess.Popen(["/bin/sh", "-c", script])
        runs = tmp_path / "runs"
        deadline = time.time() + 20
        while time.time() < deadline:
            if runs.exists() and len(runs.read_text().splitlines()) >= 3:
                break
            time.sleep(0.1)
        assert proc.poll() is None, "the wrapper exited on an agent failure"
        assert len(runs.read_text().splitlines()) >= 3
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
        assert (etc / "acquis.d" / "caddy.yaml").read_text() == "acquis\n"
        assert (etc / "parsers" / "s02-enrich" /
                "canasta-whitelists.yaml").read_text() == "whitelist\n"


FAKE_KUBECTL = """#!/bin/sh
echo "$*" >> "$(dirname "$0")/kubectl.log"
case "$*" in
  *component=caddy*) echo caddy-pod ;;
  *component=crowdsec*) echo crowdsec-pod ;;
esac
"""


class TestCscliTargets:
    def _prefix(self, tmp_path, target=None):
        bindir = tmp_path / "bin"
        bindir.mkdir()
        kubectl = bindir / "kubectl"
        kubectl.write_text(FAKE_KUBECTL)
        kubectl.chmod(0o755)
        include = {"ansible.builtin.include_tasks":
                   os.path.join(ORCH, "crowdsec_cscli_prefix.yml")}
        if target:
            include["vars"] = {"_cscli_target": target}
        tasks = [include, {"ansible.builtin.copy": {
            "content": "{{ _cscli_argv | to_json }}",
            "dest": str(tmp_path / "argv.json"), "mode": "0600"}}]
        rc, out = _play(tmp_path, tasks, {
            "instance_id": "demo", "instance_path": str(tmp_path),
            "instance_is_k8s": True, "compose_command": "docker compose"},
            {"PATH": "%s:%s" % (bindir, os.environ["PATH"])})
        assert rc == 0, out
        return yaml.safe_load((tmp_path / "argv.json").read_text())

    def test_default_targets_the_local_api_pod(self, tmp_path):
        assert self._prefix(tmp_path) == [
            "kubectl", "exec", "crowdsec-pod", "-n", "canasta-demo",
            "-c", "crowdsec", "--"]

    def test_agent_targets_the_caddy_pod_sidecar(self, tmp_path):
        assert self._prefix(tmp_path, "agent") == [
            "kubectl", "exec", "caddy-pod", "-n", "canasta-demo",
            "-c", "crowdsec-agent", "--"]


class TestEngineRestart:
    def test_kubernetes_rolls_only_the_local_api(self):
        with open(os.path.join(ORCH, "crowdsec_restart_engine.yml")) as f:
            text = f.read()
        assert "deployment/canasta-{{ instance_id }}-crowdsec" in text
        assert "-caddy" not in text

    def test_autoenroll_waits_for_the_local_api(self):
        with open(os.path.join(ORCH, "crowdsec_autoenroll.yml")) as f:
            text = f.read()
        assert "rollout status deployment/canasta-{{ instance_id }}-crowdsec" \
            in text
