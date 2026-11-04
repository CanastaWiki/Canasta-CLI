"""`canasta upgrade` must stage the gitops files it edits, and edit no
shared file for a CLI's own image pin.

On Kubernetes the image bump is persisted to hosts/<host>/vars.yaml so it
survives the next render. Left unstaged, that edit blocks the next
`canasta gitops pull` (which refuses a dirty tree) and is invisible to
`canasta gitops push` (which only commits what is staged). On Compose the
render gives each host its own CLI default, so the bump stays in .env and
the shared env.template is left as it is.
"""

import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPGRADE_IMAGE_TAG = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml"
)


def _flatten(tasks):
    for t in tasks:
        yield t
        yield from _flatten(t.get("block") or [])


def _blocks():
    """Map top-level task name -> every task nested under it, in order."""
    with open(UPGRADE_IMAGE_TAG) as fh:
        tasks = yaml.safe_load(fh)
    return {t.get("name"): list(_flatten(t.get("block") or [])) for t in tasks}


def _cmd(task):
    c = task.get("ansible.builtin.command") or task.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


def _index(block, name):
    return next((i for i, t in enumerate(block) if t.get("name") == name), None)


def test_compose_bump_neither_writes_nor_stages_env_template():
    block = _blocks()["Update Compose image tag"]
    names = [t.get("name") for t in block]
    assert "Persist CANASTA_IMAGE to env.template (durable across pulls)" not in names
    assert not any("git add" in _cmd(t) for t in block)
    assert _index(block, "Report the pending gitops push") is None


def test_kubernetes_stages_host_vars_after_writing_it():
    block = _blocks()["Update Kubernetes image tag in values.yaml"]
    write = _index(block, "Persist image_tag to gitops vars.yaml (durable across pulls)")
    stage = _index(block, "Stage the bumped image_tag")
    assert write is not None, "expected the vars.yaml persist task"
    assert stage is not None, "upgrade must stage hosts/<host>/vars.yaml on gitops"
    assert stage > write, "staging must follow the write"
    assert "git add hosts/" in _cmd(block[stage])
    assert "_upgrade_k8s_gitops_stat" in str(block[stage].get("when"))


def test_kubernetes_reports_the_pending_push():
    for name in ("Update Kubernetes image tag in values.yaml",):
        block = _blocks()[name]
        report = _index(block, "Report the pending gitops push")
        assert report is not None, f"{name} must tell the operator a push is pending"
        msg = str(block[report].get("ansible.builtin.debug", {}).get("msg", ""))
        assert "canasta gitops push" in msg
