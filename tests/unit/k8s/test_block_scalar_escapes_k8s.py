"""Escape sequences in Jinja string literals reach Kubernetes-rendered values.

The .env embedded in the ConfigMap keeps real newlines after the secret
keys are filtered out, and a Kubernetes instance's refreshed .gitignore
gets each shipped rule on its own line and loses a merged line left by an
earlier upgrade.
"""

import base64
import os
import subprocess

import pytest

from test_block_scalar_escapes import (
    REFRESH,
    REPO_ROOT,
    _fact,
    _rules,
    _run,
    _task,
)


K8S_SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_sync_config.yml")


class TestK8sEnvConfigMap:
    def test_secret_filtered_env_keeps_real_newlines(self, tmp_path):
        env = "MW_SITE_SERVER=https://example.com\nDB_PASSWORD=x\nFOO=bar\n"
        task = _task(K8S_SYNC,
                     "Filter secret-bearing keys out of .env for ConfigMap "
                     "embedding")
        got = _fact(tmp_path, task, "_sync_env_no_secrets", {
            "_sync_env": {"content": base64.b64encode(env.encode()).decode()},
            "canasta_secret_key_regex": "^[A-Z0-9_]*PASSWORD=",
        })
        assert got == "MW_SITE_SERVER=https://example.com\nFOO=bar\n"


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

    def test_each_shipped_rule_is_its_own_line_kubernetes(self, tmp_path):
        inst = self._instance(tmp_path, "mine/\n")
        lines = self._refresh(tmp_path, inst, "kubernetes")
        assert self.LAST_DEFAULT in lines
        for rule in _rules("gitignore.kubernetes"):
            assert rule in lines
        assert not any("\\n" in ln for ln in lines)

    @pytest.mark.parametrize("orch, merged", [
        ("kubernetes", LAST_DEFAULT + "\\n"),
        ("kubernetes", LAST_DEFAULT + "\\n# Kubernetes only"),
    ])
    def test_merged_line_from_earlier_upgrades_is_removed(
            self, tmp_path, orch, merged):
        inst = self._instance(tmp_path, "mine/\n%s\nother\\nrule\n" % merged)
        lines = self._refresh(tmp_path, inst, orch)
        assert merged not in lines, orch
        assert lines[:2] == ["mine/", "other\\nrule"], orch
        assert self.LAST_DEFAULT in lines, orch
