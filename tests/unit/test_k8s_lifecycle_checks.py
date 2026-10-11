"""Kubernetes lifecycle checks: crowdsec reload, delete, create, worker join.

- `crowdsec reload` must deliver an edited whitelists.yaml to the engine,
  which on Kubernetes reads it from a ConfigMap seeded at caddy pod start.
- `delete` must not report success while the namespace is still terminating.
- `create` must give the pods the MW_SECRET_KEY that .env holds.
- `install k8s-worker` must wait for the new node itself to be Ready.
"""

import os
import shutil
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")
RELOAD = os.path.join(REPO_ROOT, "roles", "crowdsec", "tasks", "reload.yml")
HELM_UNINSTALL = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                              "helm_uninstall.yml")
K8S_SETUP = os.path.join(REPO_ROOT, "roles", "create", "tasks",
                         "_kubernetes_setup.yml")
K3S_WORKER = os.path.join(REPO_ROOT, "roles", "install", "tasks",
                          "k3s_worker.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _task(tasks, name):
    return next(t for t in tasks if t.get("name") == name)


def _run(tmp_path, tasks, play_vars=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"ansible_python_interpreter": sys.executable},
                     **(play_vars or {})),
        "tasks": tasks,
    }]))
    # Other tests' CLI runs leave ANSIBLE_* settings (output callback and
    # the like) in os.environ; start from a clean slate.
    base = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env = dict(base, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def _caddy_annotations(*args):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    cmd = ["helm", "template", "canasta", CHART,
           "--set", "instance.id=test", "--set", "domains[0]=localhost",
           "--show-only", "templates/deployment-caddy.yaml", *args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("helm template failed: %s" % out.stderr.strip()[:200])
    doc = yaml.safe_load(out.stdout)
    return doc["spec"]["template"]["metadata"].get("annotations") or {}


class TestCaddyRollsOnCrowdsecConfig:
    def test_a_whitelist_change_changes_the_checksum(self):
        before = _caddy_annotations("--set", "crowdsec.enabled=true")
        after = _caddy_annotations(
            "--set", "crowdsec.enabled=true",
            "--set-string", "configData.crowdsec.whitelists\\.yaml=ip: [10.0.0.1]")
        assert before["checksum/crowdsec-config"] != after["checksum/crowdsec-config"]

    def test_a_missing_key_hashes_like_the_chart_default(self):
        default = _caddy_annotations("--set", "crowdsec.enabled=true")
        missing = _caddy_annotations("--set", "crowdsec.enabled=true",
                                     "--set", "configData.crowdsec=null")
        assert default["checksum/crowdsec-config"] == missing["checksum/crowdsec-config"]

    def test_no_crowdsec_checksum_without_crowdsec(self):
        assert "checksum/crowdsec-config" not in _caddy_annotations()


class TestCrowdsecReloadSyncsConfig:
    def test_kubernetes_deploys_the_config_before_restarting(self):
        tasks = _load(RELOAD)
        names = [t.get("name") for t in tasks]
        apply = _task(tasks, "Apply edited CrowdSec config (Kubernetes)")
        assert apply["when"] == "instance_is_k8s | bool"
        inner = _task(apply["block"], "Sync config and deploy")
        assert inner["ansible.builtin.include_role"] == {
            "name": "orchestrator", "tasks_from": "apply_config.yml"}
        assert names.index("Apply edited CrowdSec config (Kubernetes)") \
            < names.index("Restart the CrowdSec engine")

    @pytest.mark.parametrize("before,after,rolled", [
        ("caddy-a", "caddy-b", True),
        ("caddy-a", "caddy-a", False),
        ("", "caddy-b", False),
        (None, None, False),
    ])
    def test_restart_only_when_the_deploy_did_not_roll(
            self, tmp_path, before, after, rolled):
        tasks = _load(RELOAD)
        note = _task(tasks, "Note whether the deploy already rolled the engine")
        restart = _task(tasks, "Restart the CrowdSec engine")
        play_vars = {}
        if before is not None:
            play_vars["_crowdsec_pods_before"] = {"stdout": before}
            play_vars["_crowdsec_pods_after"] = {"stdout": after}
        proc = _run(tmp_path, [
            note,
            {"ansible.builtin.assert": {"that": [
                "(%s) == %s" % (restart["when"], not rolled)]}},
        ], play_vars)
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestDeleteWaitsForNamespace:
    def _tail(self):
        tasks = _load(HELM_UNINSTALL)
        return [_task(tasks, "Fail if the namespace is still terminating"),
                _task(tasks, "Warn that the namespace remains")]

    def test_fails_while_the_namespace_remains(self, tmp_path):
        proc = _run(tmp_path, self._tail(), {
            "_ns_gone": {"rc": 0}, "_k8s_namespace": "canasta-x",
            "instance_id": "x"})
        assert proc.returncode != 0
        assert "still terminating" in proc.stdout, proc.stdout + proc.stderr
        assert "kubectl get namespace canasta-x" in proc.stdout

    def test_force_warns_and_continues(self, tmp_path):
        proc = _run(tmp_path, self._tail(), {
            "_ns_gone": {"rc": 0}, "_k8s_namespace": "canasta-x",
            "instance_id": "x", "force": True})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Warning: namespace 'canasta-x'" in proc.stdout

    def test_a_gone_namespace_passes(self, tmp_path):
        proc = _run(tmp_path, self._tail(), {
            "_ns_gone": {"rc": 1}, "_k8s_namespace": "canasta-x",
            "instance_id": "x"})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Warning" not in proc.stdout

    def test_delete_deregisters_only_after_destroy(self):
        tasks = _load(os.path.join(REPO_ROOT, "roles", "delete", "tasks",
                                   "main.yml"))
        names = [t.get("name") for t in tasks]
        assert names.index("Destroy containers and volumes") \
            < names.index("Save connection state before controller deregistration")


class TestCreateUsesEnvSecretKey:
    def _expr(self):
        task = _task(_load(K8S_SETUP), "Apply K8s secrets")
        return task["vars"]["_mw_secret_key"]

    def _key(self, tmp_path, env):
        proc = _run(tmp_path, [
            {"ansible.builtin.set_fact": {"_key": self._expr()}},
            {"ansible.builtin.debug": {"msg": "KEY=[{{ _key }}]"}},
        ], {"_k8s_env_for_values": {"variables": env}})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc.stdout.split("KEY=[", 1)[1].split("]", 1)[0]

    def test_the_env_key_reaches_the_secret(self, tmp_path):
        assert self._key(tmp_path, {"MW_SECRET_KEY": "abc123"}) == "abc123"

    def test_a_key_is_generated_when_env_has_none(self, tmp_path):
        key = self._key(tmp_path, {})
        assert len(key) == 64
        assert all(c in "0123456789abcdefABCDEF" for c in key)


class TestWorkerJoinWaitsForTheNewNode:
    def _until(self):
        task = _task(_load(K3S_WORKER), "Wait for the new worker to be Ready on cp")
        assert "_worker_node_name" in task["ansible.builtin.command"]["argv"][-1]
        return task["until"]

    @pytest.mark.parametrize("rc,stdout,ready", [
        (0, "w1   Ready    <none>   1m   v1.33.1+k3s1", True),
        (0, "w1   NotReady <none>   1m   v1.33.1+k3s1", False),
        (0, "w1   Ready,SchedulingDisabled <none> 1m v1.33.1+k3s1", False),
        (1, "", False),
    ])
    def test_ready_means_this_node_is_exactly_ready(
            self, tmp_path, rc, stdout, ready):
        proc = _run(tmp_path, [
            {"ansible.builtin.assert": {"that": [
                "(%s) == %s" % (self._until(), ready)]}},
        ], {"_cp_worker_node": {"rc": rc, "stdout": stdout}})
        assert proc.returncode == 0, proc.stdout + proc.stderr
