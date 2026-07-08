"""Guards for K8s config-file pruning on gitops push.

On K8s, an instance's settings/caddy/crowdsec files are synced into the
web/caddy/varnish/crowdsec ConfigMaps via configData in the committed
values.yaml (Argo CD renders those ConfigMaps). push_kubernetes.yml rebuilds
values-configdata.yaml fresh from the current files, then merges it over the
previously-committed values.yaml.

That merge MUST replace the configData maps, not deep-merge them: values.yaml
already carries the previous push's configData keys, so a recursive merge
unions the fresh configData into the stale one and a file deleted from the
instance dir keeps its ConfigMap key forever — the removed setting keeps
applying on K8s (unlike Compose, where the bind mount reflects the deletion).
These tests lock the non-recursive merge in place; the runtime behavior is
covered by the live K8s e2e.
"""

import os

import yaml
from ansible.plugins.filter.core import combine

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PUSH = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "push_kubernetes.yml")


def _tasks():
    with open(PUSH) as f:
        return yaml.safe_load(f)


def _merge_task():
    task = next(
        t for t in _tasks()
        if "Merge fresh configData" in (t.get("name") or "")
    )
    return task["ansible.builtin.set_fact"]["_push_merged_values"]


def test_merge_is_not_recursive():
    # A recursive combine unions the configData maps and never drops a key, so
    # a deleted settings file lingers in the committed values.yaml. Guard
    # against recursive=True creeping back into any combine.
    expr = _merge_task()
    assert "recursive=True" not in expr and "recursive = True" not in expr, (
        "the configData/sidecars merge must be non-recursive so the fresh "
        "values-configdata.yaml replaces (not unions) the persisted configData"
    )
    # configData + sidecars + derived domains, each replacing non-recursively.
    assert expr.count("combine(") == 3, (
        "expected the merge to combine values-configdata, values-sidecars, "
        "and the derived domains over the persisted values"
    )


def test_stale_configmap_key_is_pruned_by_the_merge():
    # Reproduce the merge: a previous push baked Foo.php into the committed
    # values.yaml; the fresh configData (Foo.php since deleted) must win.
    persisted = {
        "web": {"replicaCount": 1},
        "configData": {
            "web": {
                "wikis.yaml": "x",
                "settings--global--Foo.php": "wfLoadExtension('Foo');",
            },
            "db": {},
        },
        "sidecars": [{"name": "old"}],
    }
    fresh_configdata = {
        "configData": {
            "web": {"wikis.yaml": "x"},
            "env": {},
            "caddy": {},
            "varnish": {},
            "crowdsec": {},
        },
        "appSecretEnv": [],
    }
    fresh_sidecars = {"sidecars": []}

    merged = combine(combine(persisted, fresh_configdata), fresh_sidecars)

    web_keys = merged["configData"]["web"]
    assert "settings--global--Foo.php" not in web_keys, (
        "deleted settings file must not linger in the committed configData"
    )
    assert web_keys == {"wikis.yaml": "x"}
    # Unrelated top-level values.yaml keys survive the merge.
    assert merged["web"] == {"replicaCount": 1}
    # A removed sidecar is likewise pruned (the fresh list replaces).
    assert merged["sidecars"] == []
