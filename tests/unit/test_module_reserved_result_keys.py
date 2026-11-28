"""Custom modules must not return keys Ansible reserves in task results.

Ansible consumes `skipped`, `failed` and `unreachable` itself, so a module
that returns one of them as data never delivers it to the playbook: a
reference such as `_result.skipped` then fails with "object of type 'dict'
has no attribute 'skipped'".
"""

import ast
import glob
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
# A result may set `failed: True` to fail the task on purpose, so `failed`
# is only flagged where a successful exit returns it.
RESERVED_AS_DATA = {"skipped", "unreachable"}
RESERVED_ON_SUCCESS = RESERVED_AS_DATA | {"failed"}


def _reserved_keys(path):
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            reserved = (RESERVED_ON_SUCCESS if name == "exit_json"
                        else RESERVED_AS_DATA if name == "dict" else set())
            for kw in node.keywords:
                if kw.arg in reserved:
                    yield kw.arg, node.lineno
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if (isinstance(key, ast.Constant)
                        and key.value in RESERVED_AS_DATA):
                    yield key.value, node.lineno


def test_no_module_returns_a_reserved_key():
    modules = glob.glob(os.path.join(REPO_ROOT, "roles", "*", "library", "*.py"))
    assert modules
    offenders = [
        "%s:%d %s" % (os.path.relpath(p, REPO_ROOT), line, key)
        for p in modules
        for key, line in _reserved_keys(p)
    ]
    assert offenders == [], offenders


def test_sidecar_migrate_reports_unmigrated_services():
    with open(os.path.join(REPO_ROOT, "playbooks", "sidecar_migrate.yml")) as f:
        playbook = f.read()
    assert "_migrate.unmigrated" in playbook
    assert "_migrate.skipped" not in playbook


def test_sidecar_migrate_report_renders_unmigrated_services(tmp_path):
    with open(os.path.join(REPO_ROOT, "playbooks", "sidecar_migrate.yml")) as f:
        tasks = yaml.safe_load(f)
    report = [t for t in tasks
              if t.get("name") == "Report services left in the override"]
    assert len(report) == 1
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"_migrate": {"unmigrated": [
            {"name": "solr", "reasons": ["uses build:", "uses devices:"]},
            {"name": "mailer", "reasons": ["already declared"]},
        ]}, "ansible_python_interpreter": sys.executable},
        "tasks": report,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "solr — uses build:, uses devices:" in out, out
    assert "mailer — already declared" in out, out
