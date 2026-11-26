"""CANASTA_ELASTICSEARCH_HEAP on Kubernetes.

The chart derives ES_JAVA_OPTS and the container's memory request and
limit from elasticsearch.heap, a deploy-time reconcile copies the key from
.env into values.yaml wherever the Caddy reconcile runs, and a gitops chart
refresh drops the former fixed memory defaults.
"""

import os
import sys

import pytest
import yaml
from test_elasticsearch_heap import (
    CHART,
    REPO_ROOT,
    _load,
    _read,
    _render,
    _run_play,
)

sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_gitops import canasta_chart_values_refresh  # noqa: E402


RECONCILE = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                         "k8s_reconcile_elasticsearch.yml")


def _es_container(tmp_path, es):
    out = _render(tmp_path, es)
    assert out.returncode == 0, out.stderr
    spec = yaml.safe_load(out.stdout)["spec"]["template"]["spec"]
    return next(c for c in spec["containers"] if c["name"] == "elasticsearch")


def _java_opts(container):
    return next(e["value"] for e in container["env"] if e["name"] == "ES_JAVA_OPTS")


class TestChart:
    def test_default_heap_keeps_the_previous_sizing(self, tmp_path):
        c = _es_container(tmp_path, {})
        assert _java_opts(c) == "-Xms256m -Xmx256m"
        assert c["resources"]["requests"] == {"cpu": "250m", "memory": "512Mi"}
        assert c["resources"]["limits"] == {"cpu": "500m", "memory": "1024Mi"}

    def test_gigabyte_heap_scales_the_memory(self, tmp_path):
        c = _es_container(tmp_path, {"heap": "4g"})
        assert _java_opts(c) == "-Xms4g -Xmx4g"
        assert c["resources"]["requests"]["memory"] == "8192Mi"
        assert c["resources"]["limits"]["memory"] == "16384Mi"

    def test_operator_memory_wins(self, tmp_path):
        c = _es_container(tmp_path, {"heap": "1g", "resources": {
            "limits": {"memory": "3Gi"}}})
        assert c["resources"]["limits"] == {"cpu": "500m", "memory": "3Gi"}
        assert c["resources"]["requests"] == {"cpu": "250m", "memory": "2048Mi"}

    def test_invalid_heap_fails_the_render(self, tmp_path):
        out = _render(tmp_path, {"heap": "4gb"})
        assert out.returncode != 0
        assert "elasticsearch.heap is '4gb'" in out.stderr


class TestReconcile:
    def _reconcile(self, tmp_path, env_text, values):
        inst = tmp_path / "inst"
        inst.mkdir()
        (inst / ".env").write_text(env_text)
        (inst / "values.yaml").write_text(yaml.safe_dump(values))
        code, out = _run_play(
            tmp_path, [{"ansible.builtin.include_tasks": RECONCILE}],
            {"instance_path": str(inst), "canasta_root": REPO_ROOT})
        assert code == 0, out
        return yaml.safe_load((inst / "values.yaml").read_text())

    def test_env_heap_is_written(self, tmp_path):
        values = self._reconcile(tmp_path, "CANASTA_ELASTICSEARCH_HEAP=4g\n",
                                 {"elasticsearch": {"enabled": True,
                                                    "image": "x/es:1"}})
        assert values["elasticsearch"] == {"enabled": True, "image": "x/es:1",
                                           "heap": "4g"}

    def test_without_the_key_the_chart_default_applies(self, tmp_path):
        chart_heap = _load(os.path.join(CHART, "values.yaml"))["elasticsearch"]["heap"]
        values = self._reconcile(tmp_path, "MW_SITE_FQDN=example.com\n",
                                 {"elasticsearch": {"heap": "4g"}})
        assert values["elasticsearch"]["heap"] == chart_heap

    def test_empty_value_is_the_chart_default(self, tmp_path):
        values = self._reconcile(tmp_path, "CANASTA_ELASTICSEARCH_HEAP=\n",
                                 {"instance": {"id": "t1"}})
        assert values["elasticsearch"]["heap"] == "256m"
        assert values["instance"] == {"id": "t1"}

    @pytest.mark.parametrize("path, caddy_reconcile", [
        ("roles/orchestrator/tasks/start.yml", "k8s_reconcile_caddy.yml"),
        ("roles/create/tasks/_start.yml", "k8s_reconcile_caddy.yml"),
        ("roles/config/tasks/set.yml", "k8s_reconcile_caddy.yml"),
        ("roles/config/tasks/unset.yml", "k8s_reconcile_caddy.yml"),
    ])
    def test_runs_wherever_the_caddy_reconcile_does(self, path, caddy_reconcile):
        text = _read(path)
        assert caddy_reconcile in text
        assert "k8s_reconcile_elasticsearch.yml" in text


class TestGitopsChartRefresh:
    def test_legacy_repo_drops_the_former_fixed_memory(self):
        shipped = _load(os.path.join(CHART, "values.yaml"))
        current = {"elasticsearch": {"resources": {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": "500m", "memory": "1Gi"}}}}
        former = {"elasticsearch.resources.requests.memory": ["512Mi"],
                  "elasticsearch.resources.limits.memory": ["1Gi"]}
        out = canasta_chart_values_refresh(current, shipped, None, former)
        assert out["elasticsearch"]["resources"] == {
            "requests": {"cpu": "250m"}, "limits": {"cpu": "500m"}}
        assert out["elasticsearch"]["heap"] == "256m"

    def test_refresh_lists_the_former_memory_defaults(self):
        text = _read("roles", "gitops", "tasks", "refresh_chart.yml")
        assert "'elasticsearch.resources.requests.memory': ['512Mi']" in text
        assert "'elasticsearch.resources.limits.memory': ['1Gi']" in text
