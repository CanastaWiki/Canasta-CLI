"""The orchestrator is tested in one place per language.

Registry reads hand back `kubernetes` for an entry stored with the `k8s`
alias. Tasks read canasta.yml's `instance_is_k8s`, and Python reads
`_is_k8s()`; neither re-spells the alias list.
"""

import json
import os
import re
import subprocess
import sys

import pytest
import yaml

import canasta_registry
from mock_ansible import run_module_with_params

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers  # noqa: E402

# The two definitions are the only files that spell the alias list.
ALLOWED = {
    "canasta.yml",
    "roles/common/module_utils/canasta_config.py",
}

INLINE_CHECKS = [
    re.compile(r"""\[\s*['"]kubernetes['"]\s*,\s*['"]k8s['"]\s*\]"""),
    re.compile(r"""\(\s*['"]kubernetes['"]\s*,\s*['"]k8s['"]\s*\)"""),
    re.compile(r"""orchestrator\b[^\n]*?\)?\s*[!=]=\s*['"](compose|kubernetes|k8s)['"]"""),
    re.compile(r"""orchestrator['"]\s*,\s*['"]compose['"]\)\s*(==|!=|in\b|not\b)"""),
]


def _sources():
    for top in ("roles", "playbooks", "direct_commands", "filter_plugins"):
        for dirpath, _, files in os.walk(os.path.join(REPO_ROOT, top)):
            for name in files:
                if name.endswith((".yml", ".yaml", ".py", ".j2")):
                    yield os.path.relpath(os.path.join(dirpath, name), REPO_ROOT)
    yield "canasta.yml"
    yield "canasta.py"


def test_no_inline_orchestrator_checks():
    found = []
    for rel in _sources():
        if rel in ALLOWED:
            continue
        with open(os.path.join(REPO_ROOT, rel)) as f:
            text = f.read()
        for rx in INLINE_CHECKS:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                found.append("%s:%d: %s" % (rel, line, m.group(0)))
    assert not found, (
        "test instance_is_k8s (tasks) or _is_k8s() (Python) instead:\n"
        + "\n".join(found))


class TestPlayVar:
    def _run(self, tmp_path, play_vars, tasks):
        out = tmp_path / "out.json"
        play = tmp_path / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": dict(INSTANCE_IS_K8S_VARS, **play_vars,
                         ansible_python_interpreter=sys.executable),
            "tasks": tasks + [{"ansible.builtin.copy": {
                "content": "{{ _seen | default([]) | to_json }}",
                "dest": str(out)}}],
        }]))
        env = dict(os.environ, ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCALHOST_WARNING="0")
        playbook = os.path.join(os.path.dirname(sys.executable),
                                "ansible-playbook")
        proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                              capture_output=True, text=True, env=env,
                              cwd=tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(out.read_text())

    @staticmethod
    def _record():
        return {"ansible.builtin.set_fact": {
            "_seen": "{{ _seen | default([]) + [instance_is_k8s | bool] }}"}}

    @pytest.mark.parametrize("play_vars, expected", [
        ({}, False),
        ({"instance_orchestrator": "compose"}, False),
        ({"instance_orchestrator": "kubernetes"}, True),
        ({"instance_orchestrator": "k8s"}, True),
        ({"orchestrator": "kubernetes"}, True),
        ({"orchestrator": "compose"}, False),
        ({"orchestrator": "kubernetes", "instance_orchestrator": "compose"},
         False),
    ])
    def test_value(self, tmp_path, play_vars, expected):
        assert self._run(tmp_path, play_vars, [self._record()]) == [expected]

    def test_follows_instance_orchestrator_once_it_is_set(self, tmp_path):
        seen = self._run(tmp_path, {}, [
            self._record(),
            {"ansible.builtin.set_fact": {
                "instance_orchestrator": "kubernetes"}},
            self._record(),
        ])
        assert seen == [False, True]


class TestRegistryReads:
    @pytest.fixture
    def config_dir(self, tmp_dir):
        data = {"Instances": {
            "old": {"id": "old", "path": "/old", "orchestrator": "k8s"},
            "new": {"id": "new", "path": "/new",
                    "orchestrator": "kubernetes"},
            "web": {"id": "web", "path": "/web", "orchestrator": "compose"},
        }}
        with open(os.path.join(tmp_dir, "conf.json"), "w") as f:
            json.dump(data, f)
        return tmp_dir

    def _run(self, config_dir, **params):
        result, failed, msg = run_module_with_params(canasta_registry, dict(
            {"id": None, "path": None, "orchestrator": None,
             "dev_mode": None, "build_from": None, "host": None,
             "filter_host": None, "config_dir": config_dir}, **params))
        assert not failed, msg
        return result

    def test_query(self, config_dir):
        for inst_id, expected in (("old", "kubernetes"),
                                  ("new", "kubernetes"),
                                  ("web", "compose")):
            result = self._run(config_dir, state="query", id=inst_id)
            assert result["instance"]["orchestrator"] == expected

    def test_query_by_path(self, config_dir):
        result = self._run(config_dir, state="query_by_path", path="/old")
        assert result["instance"]["orchestrator"] == "kubernetes"

    def test_query_all(self, config_dir):
        instances = self._run(config_dir, state="query_all")["instances"]
        assert {k: v["orchestrator"] for k, v in instances.items()} == {
            "old": "kubernetes", "new": "kubernetes", "web": "compose"}

    def test_the_stored_entry_is_left_alone(self, config_dir):
        self._run(config_dir, state="query", id="old")
        self._run(config_dir, state="query_all")
        with open(os.path.join(config_dir, "conf.json")) as f:
            stored = json.load(f)["Instances"]["old"]
        assert stored["orchestrator"] == "k8s"


@pytest.mark.parametrize("inst, expected", [
    ({"orchestrator": "kubernetes"}, True),
    ({"orchestrator": "k8s"}, True),
    ({"orchestrator": "compose"}, False),
    ({"orchestrator": None}, False),
    ({}, False),
])
def test_python_helper(inst, expected):
    assert _helpers._is_k8s(inst) is expected
