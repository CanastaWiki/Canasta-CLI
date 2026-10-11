"""Integration runs must not starve the other workflows.

Every push to main starts a full integration run. Without cancellation,
back-to-back merges queued one full run each and held every runner, so
unit tests, lint, CodeQL and image builds sat queued.
"""

import os

import yaml

WORKFLOW = os.path.join(os.path.dirname(__file__), "..", "..", ".github",
                        "workflows", "integration-tests.yml")


def _workflow():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)


def test_a_newer_run_cancels_the_one_it_supersedes():
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"] == "integration-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] is True


def test_matrix_jobs_cap_their_concurrent_legs():
    jobs = _workflow()["jobs"]
    for name in ("integration-core", "integration-podman",
                 "integration-features", "integration-gitops"):
        assert jobs[name]["strategy"]["max-parallel"] == 3, name
