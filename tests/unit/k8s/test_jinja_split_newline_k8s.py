"""Line splitting in Kubernetes and Argo CD task expressions.

The expressions that read Argo CD's known hosts, the cluster state of a
failed rollout and the app Secret's env keys split with splitlines() into
the expected lines.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from test_jinja_split_newline import REPO_ROOT, _b64, _find


@pytest.mark.parametrize("path, key, variables, expected", [
    ("roles/gitops/tasks/_apply_argo_repo_secret.yml", "_existing",
     {"_argo_known_hosts": {"resources": [
         {"data": {"ssh_known_hosts": "h1 k1\nh2 k2\n"}}]}},
     ["h1 k1", "h2 k2"]),
    ("roles/orchestrator/tasks/k8s_capture_rollout_failure.yml",
     "Report cluster state for the failed rollout::msg",
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
        doc = yaml.safe_load(f)
    if "::" in key:
        name, key = key.split("::")
        doc = next(t for t in doc if t.get("name") == name)
    expr = _find(doc, key)
    rendered = Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(expr))
    assert rendered == expected
