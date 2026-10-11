"""A rejected `config set` prints its error once.

set.yml wraps the work in a block whose rescue restarts the instance if the
database password had already changed, then fails again so the command
exits non-zero. The failed task has already printed its message through
the canasta_minimal callback; a rescue that re-raised with that same
message printed it a second time.
"""

import os
import subprocess
import sys

import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _config_set(tmp_path, setting):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text("HTTP_PORT=80\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(REPO_ROOT, "vars",
                                    "secret_classification.yml")],
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "instance_orchestrator": "compose", **INSTANCE_IS_K8S_VARS,
                 "settings": [setting], "no_restart": True,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_role": {
            "name": "config", "tasks_from": "set.yml"}}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="canasta_minimal",
               ANSIBLE_CALLBACK_PLUGINS=os.path.join(REPO_ROOT,
                                                     "callback_plugins"),
               ANSIBLE_ROLES_PATH=os.path.join(REPO_ROOT, "roles"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path, stdin=subprocess.DEVNULL)


def test_a_bad_port_is_reported_once(tmp_path):
    proc = _config_set(tmp_path, "HTTP_PORT=abc")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert out.count("HTTP_PORT must be a number") == 1, out


def test_an_unknown_key_is_reported_once(tmp_path):
    proc = _config_set(tmp_path, "NOT_A_REAL_KEY=1")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert out.count("Unrecognized key 'NOT_A_REAL_KEY'") == 1, out
