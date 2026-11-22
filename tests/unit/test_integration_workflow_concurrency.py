"""Integration runs must not starve the other workflows.

Every push to main starts a full integration run. Without cancellation,
back-to-back merges queued one full run each and held every runner, so
unit tests, lint, CodeQL and image builds sat queued.
"""

import os

import yaml

WORKFLOW = os.path.join(os.path.dirname(__file__), "..", "..", ".github",
                        "workflows", "integration-tests.yml")

# The account runs at most 20 jobs at a time across all workflows. A push
# to main must leave at least one runner free for everything else.
RUNNER_LIMIT = 20

# Jobs that run on push to main without a matrix cap, and how many runners
# each holds at once. The Podman matrix job finishes before the Podman legs
# that need it start, so it never adds to the total; the remote job is two
# legs.
UNCAPPED_LEGS = {"integration-podman-matrix": 0, "remote-integration": 2}


def _workflow():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)


def _runs_on_push(job):
    condition = job.get("if")
    return condition is None or "'push'" in condition


def test_a_newer_run_cancels_the_one_it_supersedes():
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"] == "integration-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] is True


def test_matrix_jobs_cap_their_concurrent_legs():
    jobs = _workflow()["jobs"]
    for name, job in jobs.items():
        if "matrix" in job.get("strategy", {}) and name not in UNCAPPED_LEGS:
            assert "max-parallel" in job["strategy"], name


def test_a_push_run_leaves_a_runner_free():
    jobs = _workflow()["jobs"]
    total = 0
    for name, job in jobs.items():
        if not _runs_on_push(job):
            continue
        if name in UNCAPPED_LEGS:
            total += UNCAPPED_LEGS[name]
        else:
            total += job.get("strategy", {}).get("max-parallel", 1)
    assert total < RUNNER_LIMIT, total


def test_kubernetes_jobs_run_weekly_and_on_request_only():
    jobs = _workflow()["jobs"]
    k8s = [name for name in jobs if name.startswith("integration-k8s")]
    assert k8s
    for name in k8s:
        assert jobs[name]["if"] == (
            "github.event_name == 'schedule' || "
            "(github.event_name == 'workflow_dispatch' && inputs.kubernetes)"
        ), name


def test_every_kubernetes_job_is_reported():
    jobs = _workflow()["jobs"]
    k8s = sorted(name for name in jobs if name.startswith("integration-k8s"))
    report = jobs["k8s-report"]
    assert sorted(report["needs"]) == k8s
    assert "schedule" in report["if"]
    assert report["permissions"] == {"issues": "write"}
