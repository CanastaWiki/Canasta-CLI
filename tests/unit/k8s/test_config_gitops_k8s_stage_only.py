"""`canasta config set` and `config unset` stage a gitops-managed Kubernetes
instance only.

Argo CD deploys such an instance from its gitops repo and self-heals back to
it, so a direct restart is reverted until the change is pushed. These run the
config role's set.yml and unset.yml through _config_play.
"""

import subprocess

import yaml

from _config_play import _set, _unset

PUSH = ("Run 'canasta gitops push -i inst', then 'canasta gitops sync -i inst', "
        "to deploy it.")
WARNING = "This is a gitops-managed Kubernetes instance."


class TestConfigSetGitopsKubernetes:
    def test_stages_without_restarting(self, tmp_path):
        out, lines, inst = _set(tmp_path)
        assert "RESTART" not in lines
        assert "Restarting instance" not in out
        assert "RECONCILE_CADDY" in lines
        assert "RECONCILE_ES" in lines
        assert "MW_ENABLE_JOB_RUNNER=false" in (inst / ".env").read_text()
        assert ("Settings updated. The change to 'inst' is staged in its "
                "gitops repo but not deployed.") in out
        assert PUSH in out
        assert WARNING not in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines, _ = _set(tmp_path, running=False)
        assert "RESTART" not in lines
        assert "left stopped" in out
        assert "gitops sync" not in out
        assert "Run 'canasta gitops push -i inst'; the change deploys" in out

    def test_no_restart_still_points_at_push(self, tmp_path):
        out, lines, _ = _set(tmp_path, no_restart=True)
        assert "RESTART" not in lines
        assert PUSH in out
        assert "canasta reconcile" not in out

    def test_image_tag_is_staged_in_the_host_vars(self, tmp_path):
        out, _, inst = _set(
            tmp_path, setting="CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.5.28")
        host_vars = yaml.safe_load(
            (inst / "hosts" / "host1" / "vars.yaml").read_text())
        assert host_vars["image_tag"] == "3.5.28"
        assert host_vars["instance_id"] == "inst"
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                cwd=inst, check=True, capture_output=True,
                                text=True).stdout.split()
        assert staged == ["hosts/host1/vars.yaml"]
        values = yaml.safe_load((inst / "values.yaml").read_text())
        assert values["image"]["tag"] == "3.5.28"
        assert PUSH in out

    def test_internal_caller_keeps_its_restart(self, tmp_path):
        out, lines, _ = _set(tmp_path, config_set_caller_reports=True)
        assert "RESTART" in lines
        assert "RECONCILE_CADDY" not in lines
        assert "RECONCILE_ES" not in lines
        assert "staged in its gitops repo" not in out
        # The caller reports the outcome and the push it needs itself.
        assert WARNING not in out

    def test_internal_caller_that_restarts_still_warns(self, tmp_path):
        out, lines, _ = _set(tmp_path, config_restart_by_caller=True,
                             no_restart=True)
        assert "staged in its gitops repo" not in out
        assert WARNING in out


class TestCrowdSecEnrollmentHint:
    HINT = ("After the sync, run 'canasta crowdsec bouncer-enroll -i inst' to "
            "give Caddy its bouncer key, then 'canasta gitops push -i inst' "
            "to record it.")

    def test_enabling_crowdsec_names_the_enrollment(self, tmp_path):
        out, _, _ = _set(tmp_path, setting="CANASTA_ENABLE_CROWDSEC=true")
        assert PUSH + " " + self.HINT in out

    def test_other_settings_do_not(self, tmp_path):
        out, _, _ = _set(tmp_path)
        assert "bouncer-enroll" not in out

    def test_disabling_crowdsec_does_not(self, tmp_path):
        out, _, _ = _set(tmp_path, setting="CANASTA_ENABLE_CROWDSEC=false")
        assert "bouncer-enroll" not in out

    def test_a_stopped_instance_enrolls_when_it_starts(self, tmp_path):
        out, _, _ = _set(tmp_path, running=False,
                         setting="CANASTA_ENABLE_CROWDSEC=true")
        assert "bouncer-enroll" not in out
        assert ("the change deploys the next time it starts. That start "
                "enrolls the CrowdSec bouncer; run 'canasta gitops push -i "
                "inst' again after it so Caddy uses the bouncer.") in out

    def test_a_stopped_instance_needs_one_push_for_other_settings(
            self, tmp_path):
        out, _, _ = _set(tmp_path, running=False)
        assert "again after it" not in out


class TestConfigSetOtherInstancesStillRestart:
    def test_kubernetes_without_gitops_restarts(self, tmp_path):
        out, lines, inst = _set(tmp_path, gitops=False)
        assert "RESTART" in lines
        assert "Settings updated." in out
        assert "staged in its gitops repo" not in out
        assert not (inst / "hosts").exists()

    def test_kubernetes_image_without_gitops_writes_no_host_vars(self, tmp_path):
        _, lines, inst = _set(
            tmp_path, gitops=False,
            setting="CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.5.28")
        assert "RESTART" in lines
        assert not (inst / "hosts").exists()


class TestConfigUnsetGitopsKubernetes:
    def test_stages_without_restarting(self, tmp_path):
        out, lines, inst = _unset(tmp_path)
        assert "RESTART" not in lines
        assert "Restarting instance" not in out
        assert "RECONCILE_CADDY" in lines
        assert "RECONCILE_ES" in lines
        assert "MW_ENABLE_JOB_RUNNER" not in (inst / ".env").read_text()
        assert ("Keys removed. The change to 'inst' is staged in its gitops "
                "repo but not deployed.") in out
        assert PUSH in out
        assert WARNING not in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines, _ = _unset(tmp_path, running=False)
        assert "RESTART" not in lines
        assert "left stopped" in out
        assert "gitops sync" not in out


class TestConfigUnsetOtherInstancesStillRestart:
    def test_kubernetes_without_gitops_restarts(self, tmp_path):
        out, lines, _ = _unset(tmp_path, gitops=False)
        assert "RESTART" in lines
        assert "Keys removed." in out
        assert "staged in its gitops repo" not in out
