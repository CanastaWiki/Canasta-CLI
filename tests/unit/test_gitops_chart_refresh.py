"""Kubernetes instances keep their copies of the Helm chart current.

Argo CD renders a gitops repo's own copy of the chart, and direct deploys
render <instance>/_chart. Both must match the shipped chart after an upgrade,
including templates the chart no longer ships, and a chart-only change must
redeploy the instance.
"""

import os
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")
ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops")
SYNC_CHART = os.path.join(ORCH_TASKS, "k8s_sync_chart.yml")
REFRESH_CHART = os.path.join(GITOPS, "tasks", "refresh_chart.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks",
                            "main.yml")

sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_gitops import (  # noqa: E402
    FilterModule,
    canasta_chart_values_refresh as refresh,
)


def _read(path):
    with open(path) as f:
        return f.read()


def _load(path):
    return yaml.safe_load(_read(path))


def _iter_tasks(tasks):
    for t in tasks:
        yield t
        if isinstance(t, dict) and "block" in t:
            yield from _iter_tasks(t["block"])


def _find(path, name):
    for t in _iter_tasks(_load(path)):
        if name in t.get("name", ""):
            return t
    raise AssertionError("no task named %r in %s" % (name, path))


class TestValuesRefresh:
    def test_registered_as_filter(self):
        assert "canasta_chart_values_refresh" in FilterModule().filters()

    def test_untouched_default_follows_the_new_default(self):
        out = refresh({"varnish": {"image": "v7"}}, {"varnish": {"image": "v9"}},
                      {"varnish": {"image": "v7"}})
        assert out == {"varnish": {"image": "v9"}}

    def test_operator_setting_is_kept(self):
        out = refresh({"varnish": {"image": "mine"}},
                      {"varnish": {"image": "v9"}},
                      {"varnish": {"image": "v7"}})
        assert out == {"varnish": {"image": "mine"}}

    def test_new_chart_keys_are_filled_in(self):
        out = refresh({"a": 1}, {"a": 1, "b": {"c": 2}}, {"a": 1})
        assert out == {"a": 1, "b": {"c": 2}}

    def test_untouched_removed_key_is_dropped(self):
        old = {"jobrunner": {"enabled": True}, "a": 1}
        out = refresh(dict(old), {"a": 1}, old)
        assert out == {"a": 1}

    def test_changed_removed_key_is_kept(self):
        out = refresh({"jobrunner": {"enabled": False}}, {},
                      {"jobrunner": {"enabled": True}})
        assert out == {"jobrunner": {"enabled": False}}

    def test_keys_unknown_to_the_chart_are_kept(self):
        out = refresh({"configData": {"web": {"x": "y"}}, "extra": 1},
                      {"configData": {"web": {}}}, {"configData": {"web": {}}})
        assert out == {"configData": {"web": {"x": "y"}}, "extra": 1}

    def test_lists_compare_whole(self):
        out = refresh({"domains": ["wiki.example.com"]},
                      {"domains": ["localhost"]}, {"domains": ["localhost"]})
        assert out == {"domains": ["wiki.example.com"]}

    def test_without_a_baseline_only_listed_former_defaults_move(self):
        values = {"crowdsec": {"image": "old"}, "es": {"image": "keep"}}
        shipped = {"crowdsec": {"image": "new"}, "es": {"image": "new"}}
        out = refresh(values, shipped, None, {"crowdsec.image": ["old"]})
        assert out == {"crowdsec": {"image": "new"}, "es": {"image": "keep"}}


class TestSyncChart:
    def test_chart_directories_are_flat(self):
        """The stale-file cleanup compares basenames, so it only covers
        files directly inside templates/ and files/."""
        for sub in ("templates", "files"):
            d = os.path.join(CHART, sub)
            assert all(os.path.isfile(os.path.join(d, n))
                       for n in os.listdir(d)), sub

    def test_removes_files_the_chart_no_longer_ships(self):
        task = _find(SYNC_CHART, "Remove files the chart no longer ships")
        assert task["ansible.builtin.file"]["state"] == "absent"
        assert "_sync_chart_stale" in str(task["loop"])

    def test_copies_files_dir(self):
        task = _find(SYNC_CHART, "Copy the chart's templates and files")
        assert task["loop"] == ["templates", "files"]

    def test_direct_deploy_uses_the_sync(self):
        task = _find(os.path.join(ORCH_TASKS, "helm_deploy.yml"),
                     "Copy Helm chart to instance directory")
        assert task["ansible.builtin.include_tasks"].endswith(
            "k8s_sync_chart.yml")
        assert task["vars"]["_sync_chart_dest"].endswith("/_chart")


class TestUpgradeRedeploysOnChartChange:
    def test_stack_refresh_syncs_the_chart(self):
        path = os.path.join(ORCH_TASKS, "write_stack_files.yml")
        task = _find(path, "Refresh the instance's copy of the Helm chart")
        assert task["ansible.builtin.include_tasks"].endswith(
            "k8s_sync_chart.yml")
        assert "stack_files_force" in str(task["when"])

    def test_chart_signal_is_reset_on_every_call(self):
        tasks = _load(os.path.join(ORCH_TASKS, "write_stack_files.yml"))
        assert tasks[0]["ansible.builtin.set_fact"] == {
            "_k8s_chart_changed": False}

    def test_restart_needed_includes_chart_change(self):
        task = _find(UPGRADE_MAIN, "Decide whether a restart is needed")
        assert "_k8s_chart_changed" in str(
            task["ansible.builtin.set_fact"]["_restart_needed"])


class TestGitopsRepoChart:
    def test_upgrade_refreshes_the_repo_chart(self):
        task = _find(UPGRADE_MAIN, "Refresh the gitops repo's Helm chart")
        assert "refresh_chart.yml" in task["ansible.builtin.include_tasks"]

    def test_refresh_syncs_into_the_repo_root(self):
        task = _find(REFRESH_CHART, "Sync the shipped chart into the repo")
        assert task["vars"] == {"_sync_chart_dest": "{{ instance_path }}",
                                "_sync_chart_values": False}

    def test_refresh_stages_every_chart_path(self):
        task = _find(REFRESH_CHART, "Stage the refreshed chart")
        cmd = task["ansible.builtin.command"]["cmd"]
        for path in ("Chart.yaml", "templates", "files", "values.yaml",
                     "chart-defaults.yaml"):
            assert path in cmd.split()

    def test_elasticsearch_7_17_is_not_a_movable_default(self):
        """Its data directory cannot be opened by 7.10.2."""
        task = _find(REFRESH_CHART,
                     "Refresh values.yaml against the shipped chart defaults")
        assert "7.17.9" not in str(task["ansible.builtin.set_fact"])

    def test_init_records_the_chart_defaults(self):
        path = os.path.join(GITOPS, "tasks", "init_kubernetes.yml")
        task = _find(path, "Record the chart defaults values.yaml starts from")
        assert task["ansible.builtin.copy"]["dest"].endswith(
            "/chart-defaults.yaml")
        sync = _find(path, "Copy the Helm chart into instance directory")
        assert sync["ansible.builtin.include_tasks"].endswith(
            "k8s_sync_chart.yml")

    def test_chart_paths_are_tracked_and_cleaned_on_reinit(self):
        tracked = _load(os.path.join(GITOPS, "vars", "main.yml"))[
            "gitops_k8s_tracked_paths"]
        cleanup = _find(os.path.join(GITOPS, "tasks", "_reinit_cleanup.yml"),
                        "Kubernetes-only gitops files")["loop"]
        for path in ("Chart.yaml", "chart-defaults.yaml", "templates",
                     "files"):
            assert path in tracked
            assert path in cleanup


class TestKubernetesIgnoreRules:
    RULES = os.path.join(GITOPS, "files", "gitignore.kubernetes")

    def _rules(self):
        return [line.strip() for line in _read(self.RULES).splitlines()
                if line.strip() and not line.startswith("#")]

    def test_ignores_the_direct_deploy_output(self):
        rules = self._rules()
        assert "_chart/" in rules
        assert "values-domains.yaml" in rules

    def test_tracks_the_charts_my_cnf(self):
        """gitignore.default ignores my.cnf anywhere, which would leave the
        chart Argo CD renders without files/my.cnf."""
        assert "!/files/my.cnf" in self._rules()

    def test_init_and_refresh_use_the_kubernetes_rules(self):
        assert "gitignore.kubernetes" in _read(
            os.path.join(GITOPS, "tasks", "init_kubernetes.yml"))
        assert "'kubernetes'" in _read(
            os.path.join(GITOPS, "tasks", "refresh_gitignore.yml"))

    def test_refresh_untracks_a_committed_chart_copy(self):
        task = _find(os.path.join(GITOPS, "tasks", "refresh_gitignore.yml"),
                     "Remove the chart copy from the index")
        assert task["ansible.builtin.command"]["cmd"] == (
            "git rm -r --cached -q -- _chart")


class TestPushOwnedKeys:
    def test_kept_keys_are_left_alone(self):
        values = {"configData": {"web": {"x": "y"}}, "a": 1}
        shipped = {"configData": {"web": {}, "db": {}}, "a": 2,
                   "sidecars": []}
        out = refresh(values, shipped, {"a": 1}, None,
                      ["configData", "sidecars"])
        assert out == {"configData": {"web": {"x": "y"}}, "a": 2}

    def test_refresh_keeps_what_push_rebuilds(self):
        task = _find(REFRESH_CHART,
                     "Read the repo's values.yaml and recorded chart defaults")
        assert task["loop"][2:] == ["values-configdata.yaml",
                                    "values-sidecars.yaml"]
