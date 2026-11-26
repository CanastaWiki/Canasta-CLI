"""`canasta doctor`'s per-instance consistency checks skip a Kubernetes
instance."""

from test_doctor_consistency import doctor


class TestInstanceConsistencyLines:
    def test_skips_k8s(self):
        assert doctor._instance_consistency_lines(
            {"orchestrator": "kubernetes", "path": "/x"}) == []
