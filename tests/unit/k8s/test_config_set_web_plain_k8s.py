"""`config set --web` without --secret on Kubernetes: the keys listed in
config/env-keys-web join the web pods' env ConfigMap, secret-named keys stay
out of it, and with no list the pod env allowlist alone applies."""

import os

import yaml

from test_config_set_web_plain import (
    KEY,
    REPO_ROOT,
    _instance,
    _load,
    _play,
    _tasks_named,
)


ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


class TestKubernetes:
    SYNC = os.path.join(ORCH_TASKS, "k8s_sync_config.yml")

    def _env_config(self, tmp_path, listed):
        inst = _instance(tmp_path, lists={"config/env-keys-web": listed})
        tasks = [t for t in _load(self.SYNC)
                 if t["name"] == "Set K8s namespace and pod env allowlist"]
        tasks += _tasks_named(self.SYNC, "Read the plain web keys",
                              "List the plain web keys", "Build pod env config data")
        tasks.append({"ansible.builtin.copy": {
            "content": "{{ _env_config_data | to_json }}",
            "dest": str(inst / "out.json")}})
        proc = _play(tmp_path, inst, tasks, {
            "instance_id": "t",
            "_sync_env_dict": {"variables": {
                KEY: "false", "PM_MAX_CHILDREN": "12", "MAPS_API_KEY": "abc",
                "UNLISTED": "x"}}})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return yaml.safe_load((inst / "out.json").read_text())

    def test_listed_keys_join_the_env_configmap(self, tmp_path):
        data = self._env_config(tmp_path, "%s\n" % KEY)
        assert data == {KEY: "false", "PM_MAX_CHILDREN": "12"}

    def test_secret_named_keys_stay_out_of_the_configmap(self, tmp_path):
        data = self._env_config(tmp_path, "MAPS_API_KEY\n")
        assert "MAPS_API_KEY" not in data

    def test_no_list_keeps_the_allowlist(self, tmp_path):
        inst = _instance(tmp_path)
        tasks = _tasks_named(self.SYNC, "Read the plain web keys",
                             "List the plain web keys")
        tasks.append({"ansible.builtin.assert": {
            "that": ["_sync_web_env_keys == []"]}})
        proc = _play(tmp_path, inst, tasks)
        assert proc.returncode == 0, proc.stdout + proc.stderr
