"""`canasta upgrade` must stage the gitops files it edits.

The image bump is persisted to env.template (Compose) / hosts/<host>/vars.yaml
(K8s) so it survives the next render. Left unstaged, that edit blocks the next
`canasta gitops pull` (which refuses a dirty tree) and is invisible to
`canasta gitops push` (which only commits what is staged) — so the pin never
reaches the other hosts. Guard that both branches stage their edit.
"""

import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPGRADE_IMAGE_TAG = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml"
)


def _blocks():
    """Map top-level task name -> its inner task list."""
    with open(UPGRADE_IMAGE_TAG) as fh:
        tasks = yaml.safe_load(fh)
    return {t.get("name"): t.get("block") or [] for t in tasks}


def _cmd(task):
    c = task.get("ansible.builtin.command") or task.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


def _index(block, name):
    return next((i for i, t in enumerate(block) if t.get("name") == name), None)


def test_compose_stages_env_template_after_writing_it():
    block = _blocks()["Update Compose image tag"]
    write = _index(block, "Persist CANASTA_IMAGE to env.template (durable across pulls)")
    stage = _index(block, "Stage the bumped env.template")
    assert write is not None, "expected the env.template persist task"
    assert stage is not None, "upgrade must stage env.template on a gitops instance"
    assert stage > write, "staging must follow the write"
    assert "git add env.template" in _cmd(block[stage])
    # Off gitops there is no repo to stage into.
    assert "_upgrade_image_gitops_stat" in str(block[stage].get("when"))


def test_kubernetes_stages_host_vars_after_writing_it():
    block = _blocks()["Update Kubernetes image tag in values.yaml"]
    write = _index(block, "Persist image_tag to gitops vars.yaml (durable across pulls)")
    stage = _index(block, "Stage the bumped image_tag")
    assert write is not None, "expected the vars.yaml persist task"
    assert stage is not None, "upgrade must stage hosts/<host>/vars.yaml on gitops"
    assert stage > write, "staging must follow the write"
    assert "git add hosts/" in _cmd(block[stage])
    assert "_upgrade_k8s_gitops_stat" in str(block[stage].get("when"))


def test_each_branch_reports_the_pending_push():
    for name in (
        "Update Compose image tag",
        "Update Kubernetes image tag in values.yaml",
    ):
        block = _blocks()[name]
        report = _index(block, "Report the pending gitops push")
        assert report is not None, f"{name} must tell the operator a push is pending"
        msg = str(block[report].get("ansible.builtin.debug", {}).get("msg", ""))
        assert "canasta gitops push" in msg
