"""Custom modules must not return keys Ansible reserves in task results.

Ansible consumes `skipped`, `failed` and `unreachable` itself, so a module
that returns one of them as data never delivers it to the playbook: a
reference such as `_result.skipped` then fails with "object of type 'dict'
has no attribute 'skipped'".
"""

import ast
import glob
import os

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
