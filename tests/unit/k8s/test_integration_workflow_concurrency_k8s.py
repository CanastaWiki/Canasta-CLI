"""The Kubernetes integration jobs run weekly or on request, and each is reported.

They never run on push to main, and the k8s-report job needs every one of
them so a scheduled failure files an issue.
"""

from test_integration_workflow_concurrency import _workflow


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
