"""A failed gitops rollout during upgrade does not strand later instances.

When a stage-only upgrade's rollout restart of the first instance times
out, the second is still rolled out and the play fails naming the first.
"""

from test_upgrade_continues_past_failed_restart import _run


class TestGitopsRolloutFailure:
    def test_the_next_instance_still_upgrades_and_the_play_fails(
            self, tmp_path):
        proc, marks = _run(tmp_path, stage_only=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        rollouts = (marks / "kubectl").read_text()
        assert "rollout status deployment/canasta-second-web" in rollouts, out
        assert ("'first': restarting failed: error: deployment "
                "\"canasta-first-web\" exceeded its progress deadline") in out
        assert "Upgraded 1 instance(s). 1 of them (second)" in out, out
