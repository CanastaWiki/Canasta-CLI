"""`canasta extension|skin enable|disable` stage a gitops-managed Kubernetes
instance only.

Argo CD deploys such an instance from its gitops repo and self-heals back to
it, so a direct deploy is reverted until the change is pushed. These run the
tail of enable.yml and disable.yml through _extension_play.
"""

from _extension_play import DISABLE, ENABLE, _run

PUSH = ("Run 'canasta gitops push -i inst', then 'canasta gitops sync -i inst', "
        "to deploy it.")
STOPPED = ("The instance is stopped, so it was left stopped. Run "
           "'canasta gitops push -i inst'; the change deploys the next time "
           "it starts.")


class TestEnableGitopsKubernetes:
    def test_commits_without_deploying_or_running_update(self, tmp_path):
        out, lines = _run(tmp_path, ENABLE, wiki="docs")
        assert lines == ["COMMIT Enable"]
        assert ("Enabled extensions: Cite,Foo. The change to 'inst' is staged "
                "in its gitops repo but not deployed.") in out
        assert PUSH in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines = _run(tmp_path, ENABLE, running=False)
        assert lines == ["COMMIT Enable"]
        assert STOPPED in out
        assert "gitops sync" not in out


class TestEnableOtherInstancesStillDeploy:
    def test_applies_and_runs_update(self, tmp_path):
        out, lines = _run(tmp_path, ENABLE, gitops=False, wiki="docs")
        assert lines == ["COMMIT Enable", "APPLY", "UPDATE docs"]
        assert "Enabled extensions: Cite,Foo" in out
        assert "gitops push -i" not in out

    def test_skip_update_still_skips(self, tmp_path):
        _, lines = _run(tmp_path, ENABLE, gitops=False, skip_update=True)
        assert lines == ["COMMIT Enable", "APPLY"]


class TestDisableGitopsKubernetes:
    def test_commits_without_deploying(self, tmp_path):
        out, lines = _run(tmp_path, DISABLE)
        assert lines == ["COMMIT Disable"]
        assert ("Disabled extensions: Cite,Foo. The change to 'inst' is "
                "staged in its gitops repo but not deployed.") in out
        assert PUSH in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines = _run(tmp_path, DISABLE, running=False)
        assert lines == ["COMMIT Disable"]
        assert STOPPED in out
        assert "gitops sync" not in out


class TestDisableOtherInstancesStillDeploy:
    def test_applies(self, tmp_path):
        out, lines = _run(tmp_path, DISABLE, gitops=False)
        assert lines == ["COMMIT Disable", "APPLY"]
        assert "Disabled extensions: Cite,Foo" in out
        assert "gitops push -i" not in out
