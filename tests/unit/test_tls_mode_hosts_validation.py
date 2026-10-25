"""config set's TLS_MODE_HOSTS check, run through the real task file."""

import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VALIDATE = os.path.join(REPO_ROOT, "roles", "config", "tasks",
                        "_validate_tls_mode_hosts.yml")
VALIDATE_VALUE = os.path.join(REPO_ROOT, "roles", "config", "tasks", "_validate_value.yml")


def _validate(tmp_path, value, orchestrator="compose"):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, _config_value=value,
                     instance_orchestrator=orchestrator,
                     ansible_python_interpreter=sys.executable),
        "tasks": [{"ansible.builtin.include_tasks": VALIDATE}],
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


@pytest.mark.parametrize("value", [
    "", "origin.example.net=acme", "a.example.com=ACME, b.example.com=acme-dns",
    "www.example.org=internal,",
])
def test_accepted_values(tmp_path, value):
    code, out = _validate(tmp_path, value)
    assert code == 0, out


@pytest.mark.parametrize("value", [
    "origin.example.net", "origin.example.net=custom", "origin.example.net=off",
    "=acme", "origin.example.net=acme;www.example.org=internal", "-bad-.example=acme",
])
def test_refused_values(tmp_path, value):
    code, out = _validate(tmp_path, value)
    assert code != 0 and "TLS_MODE_HOSTS must be" in out, out


def test_config_set_runs_the_check_and_refuses_it_on_kubernetes():
    with open(VALIDATE_VALUE) as f:
        tasks = yaml.safe_load(f)
    assert any(t.get("ansible.builtin.include_tasks") == "_validate_tls_mode_hosts.yml"
               and t.get("when") == "_config_key == 'TLS_MODE_HOSTS'" for t in tasks)
    k8s_block = next(t for t in tasks if t.get("name", "").startswith("Block the edge's TLS keys"))
    assert "'TLS_MODE_HOSTS'" in " ".join(k8s_block["when"])
