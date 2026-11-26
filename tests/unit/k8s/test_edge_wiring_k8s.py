"""The edge's learn and probe steps are skipped on Kubernetes instances,
which have no edge, and learning never fails the command."""

from test_edge_wiring import _tasks


def test_learning_is_a_no_op_on_kubernetes_and_never_fails_the_command():
    task = _tasks("edge_learn.yml")[0]
    assert task["canasta_edge"]["state"] == "learn"
    assert task["when"] == "not (instance_is_k8s | bool)"
    assert task["failed_when"] is False


def test_the_probe_runs_only_for_compose():
    probe = _tasks("edge_probe.yml")[0]
    assert probe["canasta_edge"]["state"] == "probe"
    assert probe["when"] == "not (instance_is_k8s | bool)"
