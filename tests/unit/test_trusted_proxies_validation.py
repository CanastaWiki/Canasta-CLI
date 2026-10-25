"""config set's TRUSTED_PROXIES check, run through the real task file."""

import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VALIDATE = os.path.join(REPO_ROOT, "roles", "config", "tasks",
                        "_validate_trusted_proxies.yml")
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
    "", "cloudflare", "Imperva", "203.0.113.0/24, 198.51.100.4", "2001:db8::/32",
    "imperva@203.0.113.10/32,203.0.113.11/32", "Cloudflare@203.0.113.10",
    "imperva@ 203.0.113.10/32 , 2001:db8::1",
])
def test_accepted_values(tmp_path, value):
    code, out = _validate(tmp_path, value)
    assert code == 0, out


@pytest.mark.parametrize("value", [
    "cloudfalre", "imperva@", "imperva@,", "akamai@203.0.113.10/32",
    "imperva@cloudflare", "imperva@203.0.113.10@203.0.113.11", "@203.0.113.10",
    "imperva,203.0.113.10",
])
def test_refused_values(tmp_path, value):
    code, out = _validate(tmp_path, value)
    assert code != 0 and "TRUSTED_PROXIES must be" in out, out


def test_the_cdn_behind_proxies_form_is_refused_on_kubernetes(tmp_path):
    code, out = _validate(tmp_path, "imperva@203.0.113.10/32", "kubernetes")
    assert code != 0 and "Docker Compose only" in out, out
    code, out = _validate(tmp_path, "imperva", "kubernetes")
    assert code == 0, out


def test_config_set_runs_the_check():
    with open(VALIDATE_VALUE) as f:
        tasks = yaml.safe_load(f)
    assert any(t.get("ansible.builtin.include_tasks") == "_validate_trusted_proxies.yml"
               and t.get("when") == "_config_key == 'TRUSTED_PROXIES'" for t in tasks)
