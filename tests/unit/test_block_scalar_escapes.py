"""Escape sequences in Jinja string literals reach the rendered value.

Inside a `{{ }}` expression in a folded, literal, plain or single-quoted
YAML scalar, ansible-core 2.21 does not unescape string literals: '\\n'
stays a backslash and an "n", and '\\\\s' stays two backslashes. These
tests run the real tasks through ansible-playbook and check the values.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
GITOPS_ADD = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "add.yml")
REFRESH = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "refresh_gitignore.yml")
FILES = os.path.join(REPO_ROOT, "roles", "gitops", "files")


def _task(path, name):
    with open(path) as f:
        for task in yaml.safe_load(f):
            if task.get("name") == name:
                return task
    raise AssertionError("%s has no task %r" % (path, name))


def _run(tmp_path, tasks, variables):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, **variables, canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _fact(tmp_path, task, fact, variables):
    out = tmp_path / "out.json"
    _run(tmp_path, [task, {"ansible.builtin.copy": {
        "dest": str(out), "content": "{{ %s | to_json }}" % fact}}],
        variables)
    return json.loads(out.read_text())


class TestGitopsAddPaths:
    def test_leading_dot_slash_is_stripped(self, tmp_path):
        task = _task(GITOPS_ADD, "Parse the requested files")
        got = _fact(tmp_path, task, "_add_files",
                    {"files": "./config/wikis.yaml config/./x ./a/./b"})
        assert got == ["config/wikis.yaml", "config/./x", "a/./b"]


def _rules(name):
    with open(os.path.join(FILES, name)) as f:
        return [ln.strip() for ln in f
                if ln.strip() and not ln.strip().startswith("#")]


class TestRefreshGitignore:
    LAST_DEFAULT = "config/settings/wikis/*/*.local.php"

    def _instance(self, tmp_path, gitignore):
        inst = tmp_path / "inst"
        inst.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
        (inst / ".gitops-host").write_text("h\n")
        (inst / ".gitignore").write_text(gitignore)
        return inst

    def _refresh(self, tmp_path, inst, orchestrator):
        _run(tmp_path, [{"ansible.builtin.include_tasks": REFRESH}],
             {"instance_path": str(inst),
              "instance_orchestrator": orchestrator})
        return (inst / ".gitignore").read_text().splitlines()

    def test_each_shipped_rule_is_its_own_line_compose(self, tmp_path):
        inst = self._instance(tmp_path, "mine/\n")
        lines = self._refresh(tmp_path, inst, "compose")
        expected = _rules("gitignore.default") + _rules("gitignore.compose")
        assert lines == ["mine/"] + expected
        assert not any("\\n" in ln for ln in lines)

    @pytest.mark.parametrize("orch, merged", [
        ("compose", LAST_DEFAULT + "\\n"),
    ])
    def test_merged_line_from_earlier_upgrades_is_removed(
            self, tmp_path, orch, merged):
        inst = self._instance(tmp_path, "mine/\n%s\nother\\nrule\n" % merged)
        lines = self._refresh(tmp_path, inst, orch)
        assert merged not in lines, orch
        assert lines[:2] == ["mine/", "other\\nrule"], orch
        assert self.LAST_DEFAULT in lines, orch
