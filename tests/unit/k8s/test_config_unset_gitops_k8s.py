"""`config unset` on a gitops instance dispatches by orchestrator.

The Compose removal is gated off Kubernetes; on Kubernetes the committed
source (values.template.yaml -> rendered-values.yaml) can't be written
through, so the dispatcher warns the operator to push instead of silently
skipping.
"""

from test_config_unset_gitops import DISPATCH, _load, _when


def _debug_msg(task):
    d = task.get("ansible.builtin.debug") or task.get("debug") or {}
    return d.get("msg", "") if isinstance(d, dict) else ""


def test_compose_branch_is_gated_off_kubernetes():
    tasks = _load(DISPATCH)
    compose = [t for t in tasks
               if str(t.get("ansible.builtin.include_tasks", ""))
               .endswith("_remove_gitops_vars.yml")]
    assert _when(compose[0]) == "not (instance_is_k8s | bool)"


def test_k8s_branch_warns_to_push():
    tasks = _load(DISPATCH)
    k8s = [t for t in tasks
           if _when(t).startswith("instance_is_k8s | bool")]
    assert k8s, "must have a K8s-gated task"
    msg = _debug_msg(next((t for t in k8s if _debug_msg(t)), k8s[0]))
    assert "gitops push" in msg, "K8s warning must tell the user to push"
    assert "unchanged" in msg or "not" in msg.lower()


def test_k8s_branch_is_not_a_silent_noop():
    tasks = _load(DISPATCH)
    k8s = [t for t in tasks if _when(t).startswith("instance_is_k8s | bool")]
    assert any(_debug_msg(t) for t in k8s), (
        "K8s config unset on a gitops instance must surface a warning, "
        "not silently skip")
