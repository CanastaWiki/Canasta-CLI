"""Kubernetes pod lookups and wiki removal on a stopped Kubernetes instance.

k8s_get_pod.yml and the CrowdSec preflight fail with a plain message when
no matching pod is Running, and remove refuses to drop a wiki's database
on a Kubernetes instance that is not running.
"""

import os

import yaml
from test_remove_stopped_instance import (
    CROWDSEC_PREFLIGHT,
    RAW_ERROR,
    REFUSAL,
    REPO_ROOT,
    WIKIS_YAML,
    _remove_tasks,
    _remove_vars,
    _run,
)


GET_POD = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                       "k8s_get_pod.yml")


def _crowdsec_k8s_tasks():
    with open(CROWDSEC_PREFLIGHT) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    start = names.index("Read CrowdSec enablement from .env")
    end = names.index("Verify the CrowdSec local API is running (Kubernetes)")
    return tasks[start:end + 1] + [
        {"ansible.builtin.debug": {"msg": "PREFLIGHT PASSED"}}]


class TestGetPod:
    TASKS = [
        {"ansible.builtin.include_tasks": GET_POD},
        {"ansible.builtin.debug": {"msg": "POD=[{{ _k8s_pod_name }}]"}},
    ]
    VARS = {"_k8s_namespace": "canasta-site", "_k8s_component": "db"}

    def test_no_running_pod_gives_a_plain_message(self, tmp_path):
        proc, out, _ = _run(tmp_path, self.TASKS, self.VARS, "")
        assert proc.returncode != 0
        assert "No running pod found for component 'db'" in out
        assert RAW_ERROR not in out

    def test_first_running_pod_is_chosen(self, tmp_path):
        proc, out, _ = _run(tmp_path, self.TASKS, self.VARS, "db-0 \ndb-1 \n")
        assert proc.returncode == 0, out
        assert "POD=[db-0]" in out


class TestCrowdsecPreflightK8s:
    def _run(self, tmp_path, fake_out):
        inst = tmp_path / "inst"
        inst.mkdir(exist_ok=True)
        (inst / ".env").write_text("CANASTA_ENABLE_CROWDSEC=true\n")
        return _run(tmp_path, _crowdsec_k8s_tasks(), {
            "instance_path": str(inst), "instance_id": "site",
            "instance_orchestrator": "kubernetes"}, fake_out)

    def test_no_crowdsec_pod_gives_the_plain_message(self, tmp_path):
        proc, out, _ = self._run(tmp_path, "")
        assert proc.returncode != 0
        assert "local API pod is not running" in out
        assert RAW_ERROR not in out

    def test_ready_local_api_passes(self, tmp_path):
        proc, out, _ = self._run(tmp_path, "crowdsec=true |")
        assert proc.returncode == 0, out
        assert "PREFLIGHT PASSED" in out

    def test_only_the_first_crowdsec_pod_counts(self, tmp_path):
        proc, out, _ = self._run(
            tmp_path, "crowdsec=false |crowdsec=true |")
        assert proc.returncode != 0
        assert "PREFLIGHT PASSED" not in out


class TestRemoveStoppedInstance:
    def _assert_refused_unchanged(self, tmp_path, proc, out, log):
        assert proc.returncode != 0
        assert REFUSAL in " ".join(out.split())
        assert RAW_ERROR not in out
        assert "REMOVE CONTINUED" not in out
        assert " exec " not in log
        assert (tmp_path / "inst" / "config" / "wikis.yaml").read_text() \
            == WIKIS_YAML

    def test_stopped_k8s_instance_is_refused(self, tmp_path):
        proc, out, log = _run(tmp_path, _remove_tasks(),
                              _remove_vars(tmp_path, "kubernetes"), "")
        self._assert_refused_unchanged(tmp_path, proc, out, log)

    def test_running_k8s_instance_proceeds(self, tmp_path):
        tasks = _remove_tasks()
        names = [t.get("name") for t in tasks]
        tasks = tasks[:names.index(
            "Require the instance to be running to drop the wiki's databases") + 1]
        tasks.append({"ansible.builtin.debug": {"msg": "REMOVE CONTINUED"}})
        proc, out, _ = _run(tmp_path, tasks,
                            _remove_vars(tmp_path, "kubernetes"), "web\ndb\n")
        assert proc.returncode == 0, out
        assert "REMOVE CONTINUED" in out
