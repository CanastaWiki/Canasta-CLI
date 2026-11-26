"""`gitops pull` on Kubernetes resyncs the schedules whenever the pull
changed anything."""

from test_schedule import _includes_sync, _tasks


def test_pull_kubernetes_resyncs_after_a_pull():
    hits = [t for t in _tasks("roles", "gitops", "tasks",
                              "pull_kubernetes.yml") if _includes_sync(t)]
    assert hits and "_git_pull.changed" in str(hits[0]["when"])
