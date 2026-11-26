"""doctor's my.cnf and buffer-pool report skips Kubernetes instances."""

from test_doctor_db_tuning import DEFAULT_POOL, INST, _patch, doctor


class TestScope:
    def test_kubernetes_is_skipped(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t%d\t2097152\n" % DEFAULT_POOL)
        inst = dict(INST, orchestrator="kubernetes")
        assert doctor._db_tuning_lines(inst) == []
