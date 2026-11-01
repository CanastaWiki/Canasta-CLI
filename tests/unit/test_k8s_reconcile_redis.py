"""On Kubernetes a deploy-time reconcile copies CANASTA_ENABLE_REDIS and
CANASTA_REDIS_MEMORY from .env into values.yaml, so Redis set with
`create -e` (which only writes .env) is deployed too."""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHART_VALUES = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                            "canasta", "values.yaml")
RECONCILE = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                         "k8s_reconcile_redis.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _reconcile(tmp_path, env_text, values):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(env_text)
    (inst / "values.yaml").write_text(yaml.safe_dump(values))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": RECONCILE}],
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return yaml.safe_load((inst / "values.yaml").read_text())


def _chart_memory():
    with open(CHART_VALUES) as f:
        return yaml.safe_load(f)["redis"]["memory"]


def test_enabled_in_env_is_written(tmp_path):
    values = _reconcile(tmp_path, "CANASTA_ENABLE_REDIS=true\n",
                        {"instance": {"id": "t1"}})
    assert values["redis"] == {"enabled": True, "memory": _chart_memory()}
    assert values["instance"] == {"id": "t1"}


def test_memory_in_env_is_written(tmp_path):
    values = _reconcile(tmp_path,
                        "CANASTA_ENABLE_REDIS=true\nCANASTA_REDIS_MEMORY=256\n",
                        {"redis": {"enabled": False}})
    assert values["redis"] == {"enabled": True, "memory": 256}


@pytest.mark.parametrize("env_text", [
    "CANASTA_ENABLE_REDIS=false\n", "CANASTA_ENABLE_REDIS=\n", "MW_SITE_FQDN=example.com\n",
])
def test_disabled_or_unset_turns_it_off(tmp_path, env_text):
    values = _reconcile(tmp_path, env_text, {"redis": {"enabled": True, "memory": 64}})
    assert values["redis"]["enabled"] is False


@pytest.mark.parametrize("path", [
    "roles/orchestrator/tasks/start.yml",
    "roles/create/tasks/_start.yml",
    "roles/config/tasks/set.yml",
    "roles/config/tasks/unset.yml",
])
def test_runs_wherever_the_elasticsearch_reconcile_does(path):
    with open(os.path.join(REPO_ROOT, path)) as f:
        text = f.read()
    assert "k8s_reconcile_elasticsearch.yml" in text
    assert "k8s_reconcile_redis.yml" in text
