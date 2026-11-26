"""`canasta upgrade` on Kubernetes stages the image bump it writes to the
host's gitops vars.yaml and reports the pending push."""

from test_upgrade_stages_gitops_pin import _blocks, _cmd, _index


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
