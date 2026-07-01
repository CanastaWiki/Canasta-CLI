"""On a gitops-managed Kubernetes instance, `config set` writes the local
values.yaml but not the committed gitops source (values.template.yaml ->
rendered-values.yaml) that Argo CD deploys. Since a silent write-through
would require committing and pushing (i.e. `gitops push`), config set must
instead warn the operator to push. Guards config_update_gitops_vars.yml's
per-orchestrator dispatch.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DISPATCH = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "config_update_gitops_vars.yml"
)


def _tasks():
    with open(DISPATCH) as f:
        return yaml.safe_load(f)


def _when(task):
    w = task.get("when", [])
    return " ".join(w if isinstance(w, list) else [str(w)])


def _debug_msg(task):
    d = task.get("ansible.builtin.debug") or task.get("debug") or {}
    return d.get("msg", "") if isinstance(d, dict) else ""


def test_compose_branch_updates_vars():
    compose = [t for t in _tasks()
               if str(t.get("ansible.builtin.include_tasks", ""))
               .endswith("_update_gitops_vars.yml")]
    assert compose, "Compose must still update vars.yaml"
    assert "== 'compose'" in _when(compose[0])


def test_k8s_branch_warns_to_push():
    k8s = [t for t in _tasks()
           if "kubernetes" in _when(t) and "k8s" in _when(t)]
    assert k8s, "must have a K8s-gated task"
    warn = k8s[0]
    msg = _debug_msg(warn)
    assert "gitops push" in msg, "K8s warning must tell the user to push"
    # It must not claim to have persisted the change itself.
    assert "unchanged" in msg or "not" in msg.lower()


def test_k8s_branch_is_not_a_silent_noop():
    # Regression: the K8s path used to be an unmentioned no-op, silently
    # dropping the change from the gitops source.
    k8s = [t for t in _tasks() if "kubernetes" in _when(t)]
    assert any(_debug_msg(t) for t in k8s), (
        "K8s config set on a gitops instance must surface a warning, "
        "not silently skip")
