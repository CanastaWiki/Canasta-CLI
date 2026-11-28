"""CANASTA_OPENSEARCH_HEAP sizes OpenSearch. CANASTA_ELASTICSEARCH_HEAP stays a
known, host-specific key because an upgrade carries its value over to
CANASTA_OPENSEARCH_HEAP; both are validated the same way.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULTS = os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml")
CLASSIFIER = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")
VALIDATE_VALUE = os.path.join(REPO_ROOT, "roles", "config", "tasks", "_validate_value.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

KEY = "CANASTA_ELASTICSEARCH_HEAP"
HEAP_KEYS = [KEY, "CANASTA_OPENSEARCH_HEAP"]


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return f.read()


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _run_play(tmp_path, tasks, play_vars):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(play_vars, ansible_python_interpreter=sys.executable),
        "tasks": tasks,
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


class TestKey:
    def test_key_is_known(self):
        keys = {k["name"]: k for k in _load(DEFAULTS)["canasta_known_keys"]}
        assert KEY in keys
        assert keys[KEY]["group"] == "Elasticsearch"

    def test_key_is_host_specific_in_gitops(self):
        assert KEY in _load(CLASSIFIER)["canasta_host_specific_nonsecret"]


def _validate_task():
    return next(t for t in _load(VALIDATE_VALUE)
                if t.get("name") == "Validate the Elasticsearch and OpenSearch heap sizes")


@pytest.mark.parametrize("key", HEAP_KEYS)
class TestValidation:
    @pytest.mark.parametrize("value", ["512m", "768M", "4g", "16G"])
    def test_accepted(self, tmp_path, key, value):
        code, out = _run_play(tmp_path, [_validate_task()],
                              {"_config_key": key, "_config_value": value})
        assert code == 0, out

    @pytest.mark.parametrize("value", ["", "4", "4gb", "0m", "1.5g", "-1g", "4 g", "4k"])
    def test_refused(self, tmp_path, key, value):
        code, out = _run_play(tmp_path, [_validate_task()],
                              {"_config_key": key, "_config_value": value})
        assert code != 0 and key + " must be a whole number" in out, out

    def test_unset_is_not_validated(self, tmp_path, key):
        code, out = _run_play(tmp_path, [_validate_task()], {"_config_key": key})
        assert code == 0, out
