"""A failed K8s create removes only a namespace it created.

Validation refuses when namespace canasta-<id> already exists; that
namespace may belong to a live instance managed elsewhere, so the rescue
must not uninstall its release or delete it.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CREATE = os.path.join(REPO_ROOT, "playbooks", "create.yml")
MAIN = os.path.join(REPO_ROOT, "roles", "create", "tasks", "main.yml")

K8S_CLEANUP = (
    "Clean up on failure (uninstall Helm release)",
    "Clean up on failure (delete K8s namespace)",
)


def _rescue_tasks():
    with open(CREATE) as f:
        doc = yaml.safe_load(f)
    for play in doc:
        if isinstance(play, dict) and "rescue" in play:
            return play["rescue"]
    raise AssertionError("create.yml has no rescue block")


def test_k8s_cleanup_requires_ownership():
    tasks = {t.get("name"): t for t in _rescue_tasks()}
    for name in K8S_CLEANUP:
        assert "_create_owns_namespace | default(false) | bool" in \
            tasks[name]["when"], name


def test_ownership_is_marked_after_validation():
    with open(MAIN) as f:
        names = [t.get("name") for t in yaml.safe_load(f)]
    validate = names.index("Validate inputs and check for stale state")
    mark = names.index("Mark the Kubernetes namespace as this create's")
    assert mark == validate + 1
