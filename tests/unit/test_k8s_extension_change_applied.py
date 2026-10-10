"""On Kubernetes an extension or skin change must reach the running pods.

Pods copy their config in at startup, so enabling an extension changed
config/settings/ on the host while the web pods kept running without it,
and the schema update then ran in a pod where it was not loaded.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
EXT_TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")
ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _names(tasks):
    return [t.get("name", "") for t in tasks]


def _apply_index(tasks):
    return next(i for i, t in enumerate(tasks)
                if (t.get("ansible.builtin.include_role") or {}).get(
                    "tasks_from") == "apply_config.yml")


def test_enable_applies_the_change_before_update_php():
    tasks = _load(os.path.join(EXT_TASKS, "enable.yml"))
    names = _names(tasks)
    apply_i = _apply_index(tasks)
    assert names.index("Enable {{ _item_type }}") < apply_i
    assert apply_i < names.index("Run update.php for each affected wiki")
    assert tasks[apply_i]["when"] == "_settings_change is changed"


def test_disable_applies_the_change():
    tasks = _load(os.path.join(EXT_TASKS, "disable.yml"))
    apply_i = _apply_index(tasks)
    assert _names(tasks).index("Disable {{ _item_type }}") < apply_i
    assert tasks[apply_i]["when"] == "_settings_change is changed"


def test_apply_is_a_no_op_on_compose():
    task = _load(os.path.join(ORCH_TASKS, "apply_config.yml"))[0]
    assert task["ansible.builtin.include_tasks"] == "k8s_apply_config.yml"
    assert "kubernetes" in task["when"]


def test_k8s_apply_syncs_deploys_then_waits_for_replaced_pods():
    names = _names(_load(os.path.join(ORCH_TASKS, "k8s_apply_config.yml")))
    order = ["Sync config to ConfigMaps", "Deploy the synced config",
             "List the web pods being replaced",
             "Wait for the replaced web pods to go"]
    assert [names.index(n) for n in order] == sorted(
        names.index(n) for n in order)


def _running(rc, stdout):
    tasks = _load(os.path.join(ORCH_TASKS, "k8s_apply_config.yml"))
    expr = next(t for t in tasks
                if t.get("name") == "Decide whether to apply the config now")[
        "ansible.builtin.set_fact"]["_apply_running"]
    templar = Templar(loader=DataLoader(), variables={
        "_apply_replicas": {"rc": rc, "stdout": stdout}})
    return str(templar.template(trust_as_template(expr))).strip() == "True"


def test_a_stopped_instance_is_left_stopped():
    assert _running(0, "1")
    assert _running(0, "2")
    assert not _running(0, "0")
    assert not _running(1, "")
