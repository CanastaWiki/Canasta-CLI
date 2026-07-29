"""CI has to exercise Podman, not just Docker.

Podman is a supported container runtime, but for a long time every
integration job ran on Docker. A Podman-only regression therefore
produced two green signals -- the PR checks and the post-merge run --
and shipped anyway; five such defects were found by hand instead.

These guards are deliberately structural rather than behavioral: what
the lane proves can only be proven by running it, but its *existence*
and its shape are cheap to pin so the coverage cannot quietly go away
again.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "tests.yml")


def _jobs():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)["jobs"]


def _run_steps(job):
    return [s.get("run", "") for s in job.get("steps", [])]


class TestPodmanLane:
    def test_a_podman_integration_job_exists(self):
        assert "integration-podman" in _jobs(), (
            "tests.yml must carry an integration job that runs against "
            "Podman; without one, a Podman-only regression ships green"
        )

    def test_podman_lane_installs_podman_compose(self):
        runs = " ".join(_run_steps(_jobs()["integration-podman"]))
        assert "podman-compose==" in runs, (
            "the Podman lane must install a pinned podman-compose -- the "
            "runtime's behavior differs enough between versions that an "
            "unpinned install makes the result unattributable"
        )

    def test_podman_lane_removes_docker(self):
        runs = " ".join(_run_steps(_jobs()["integration-podman"]))
        assert "docker" in runs and "mv" in runs, (
            "the Podman lane must take Docker off the runner, so a "
            "hardcoded `docker` call fails instead of quietly reaching a "
            "live daemon"
        )

    def test_podman_lane_runs_lifecycle_and_upgrade(self):
        runs = " ".join(_run_steps(_jobs()["integration-podman"]))
        assert "run_tests.py" in runs, (
            "the Podman lane must run the integration suite"
        )
        for test in ("lifecycle", "upgrade"):
            assert test in runs, (
                "the Podman lane must cover the %s leg -- it is where the "
                "known Podman defects surfaced" % test
            )

    def test_podman_lane_is_gated_like_the_other_integration_jobs(self):
        jobs = _jobs()
        assert (jobs["integration-podman"].get("if")
                == jobs["integration-core"].get("if")), (
            "the Podman lane must carry the same event gate as the other "
            "integration jobs (see #67), so it is not accidentally the "
            "only one running on pull requests"
        )


class TestBuildableRebuildIsAsserted:
    """The rebuild skip that broke on Podman was silent, so pin the assertion.

    `upgrade` reported success while buildable services were never
    rebuilt. Only a test that reads the rebuilt image back catches that,
    and it has to run on both runtimes.
    """

    def test_rebuild_assertion_runs_on_both_runtimes(self):
        jobs = _jobs()
        for job in ("integration-core", "integration-podman"):
            runs = " ".join(_run_steps(jobs[job]))
            assert "upgrade-rebuilds-buildable" in runs, (
                "%s must run the buildable-rebuild assertion" % job
            )
