"""`canasta image set-build-arg` on a Kubernetes instance refuses a base
image that is the Kubernetes build-from output tag."""

from test_image_build_args import SET_PLAYBOOK, _run


class TestSet:
    def test_rejects_the_kubernetes_output_tag_as_base(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK,
            {"settings": ["BASE_IMAGE=canasta:local"], "no_rebuild": True,
             "instance_is_k8s": True})
        assert rc != 0
        assert "collides with the build-from output tag (canasta:local)" in out
        assert "buildArgs" not in entry
