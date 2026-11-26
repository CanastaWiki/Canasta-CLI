"""`canasta upgrade` on a Kubernetes gitops instance keeps the image tag bump.

The upgrade updates image_tag in hosts/<host>/vars.yaml, so the next render
does not regenerate values.yaml from the stale tag.
"""

from test_upgrade_refresh_compose import IMAGE_TAG, _iter_tasks, _load


class TestImageTagGitopsDurable:
    @staticmethod
    def _lineinfile(task):
        return (task.get("ansible.builtin.lineinfile")
                or task.get("lineinfile")) if isinstance(task, dict) else None

    def test_k8s_bump_updates_gitops_image_tag(self):
        # K8s analog: on a gitops instance the upgrade must update
        # hosts/<host>/vars.yaml's image_tag, or render_kubernetes regenerates
        # the deployed values from the stale tag and reverts the bump.
        block = next(
            t for t in _load(IMAGE_TAG)
            if isinstance(t, dict)
            and t.get("name") == "Update Kubernetes image tag in values.yaml"
        )
        upd = next(
            (t for t in _iter_tasks(block.get("block", []))
             if self._lineinfile(t)
             and "image_tag" in str(self._lineinfile(t).get("regexp", ""))
             and "vars.yaml" in str(self._lineinfile(t).get("path", ""))),
            None,
        )
        assert upd is not None, (
            "K8s upgrade must update image_tag in hosts/<host>/vars.yaml so the "
            "bump survives a gitops pull"
        )
        assert "stat.exists" in str(upd.get("when", "")), (
            "the vars.yaml update must be gated on .gitops-host existing"
        )
