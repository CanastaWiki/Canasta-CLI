"""Line splitting in Jinja expressions uses splitlines(), not split('\\n').

Inside a `{{ }}` expression in a folded, literal, plain or single-quoted
scalar, ansible-core 2.21 treats '\\n' as a backslash and an "n", so
split('\\n') returns the whole input as one element.
"""

import base64
import glob
import os
import re

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)
BROKEN = re.compile(r"""split\(\s*(['"])\\n\1\s*\)""")


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _find(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        node = list(node.values())
    if isinstance(node, list):
        for v in node:
            found = _find(v, key)
            if found is not None:
                return found
    return None


def _yaml_files():
    for sub in ("roles", "playbooks"):
        yield from glob.glob(os.path.join(REPO_ROOT, sub, "**", "*.yml"),
                             recursive=True)


def test_no_split_on_escaped_newline_in_expressions():
    offenders = []
    for path in _yaml_files():
        with open(path) as f:
            doc = yaml.safe_load(f)
        for s in _strings(doc):
            if any(BROKEN.search(e) for e in EXPR.findall(s)):
                offenders.append(os.path.relpath(path, REPO_ROOT))
    assert not offenders, sorted(set(offenders))


def _b64(s):
    return base64.b64encode(s.encode()).decode()


@pytest.mark.parametrize("path, key, variables, expected", [
    ("roles/gitops/tasks/_apply_argo_repo_secret.yml", "_existing",
     {"_argo_known_hosts": {"resources": [
         {"data": {"ssh_known_hosts": "h1 k1\nh2 k2\n"}}]}},
     ["h1 k1", "h2 k2"]),
    ("roles/gitops/tasks/_render_mycnf.yml", "_mycnf_missing",
     {"_mycnf_missing_raw": "A\nB\n"}, ["A", "B"]),
    ("roles/gitops/tasks/_migrate_reclassified_secrets.yml", "_rc_lines",
     {"_rc_template": {"content": _b64("FOO_PASSWORD=x\nBAR_SECRET=y\n")},
      "canasta_secret_key_regex": ".*"},
     ["FOO_PASSWORD=x", "BAR_SECRET=y"]),
    ("roles/orchestrator/tasks/k8s_capture_rollout_failure.yml", "msg",
     {"_rollout_state": {"stdout": "l1\nl2"}},
     ["The rollout did not finish. What the cluster reports:", "",
      "l1", "l2"]),
    ("roles/orchestrator/tasks/k8s_sync_config.yml", "_app_secret_env",
     {"_app_secrets_web_raw": {"content": _b64("K1\nK2\n")},
      "_app_secrets_web_stat": {"stat": {"exists": True}}},
     ["K1", "K2"]),
])
def test_expression_splits_into_lines(path, key, variables, expected):
    with open(os.path.join(REPO_ROOT, path)) as f:
        expr = _find(yaml.safe_load(f), key)
    rendered = Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(expr))
    assert rendered == expected
