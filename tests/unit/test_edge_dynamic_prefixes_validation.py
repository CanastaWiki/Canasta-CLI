"""config set's CANASTA_EDGE_DYNAMIC_PREFIXES check, run through the real
task file with the repo's filter plugins."""

import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VALIDATE_KEY = os.path.join(REPO_ROOT, "roles", "config", "tasks", "_validate_key.yml")
KEY = "CANASTA_EDGE_DYNAMIC_PREFIXES"


def _validate(tmp_path, value):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, _config_key=KEY, _config_value=value,
                     instance_orchestrator="compose",
                     canasta_known_keys=[{"name": KEY}],
                     canasta_secret_prefixes=["AWS_"],
                     ansible_python_interpreter=sys.executable),
        "tasks": [{"ansible.builtin.include_tasks": VALIDATE_KEY}],
    }], allow_unicode=True), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


@pytest.mark.parametrize("value", ["", "Spezial:", "Spezial:, Spécial:,特別:", "Sp%C3%A9cial%3A"])
def test_accepted_values(tmp_path, value):
    code, out = _validate(tmp_path, value)
    assert code == 0, out


@pytest.mark.parametrize("value", ["Spe zial:", 'Spezial"', "Spezial{x}", "/", "Sp%zz"])
def test_refused_values(tmp_path, value):
    code, out = _validate(tmp_path, value)
    assert code != 0 and "%s entry" % KEY in out, out


def test_the_key_is_known():
    with open(os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml")) as f:
        keys = yaml.safe_load(f)["canasta_known_keys"]
    assert any(k["name"] == KEY and k["group"] == "HAProxy edge" for k in keys)
