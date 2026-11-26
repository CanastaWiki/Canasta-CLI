"""Structure guards for non-destructive `gitops join --reinit` on Kubernetes:
the old .git is cleaned up only after the clone succeeds, and the ".git
already exists" guard does not fire under --reinit."""

from _gitops_flow import flow_tasks
from test_gitops_reinit_preserve_git import _cmd, _include, _when


class TestJoinClonesBeforeCleanup:
    """A failed clone must not have already deleted the old .git."""

    def _assert(self, fname):
        tasks = flow_tasks(fname)
        clone_idx = next(i for i, t in enumerate(tasks)
                         if "git clone" in _cmd(t))
        cleanup_idx = next(i for i, t in enumerate(tasks)
                           if _include(t) == "_reinit_cleanup.yml")
        assert cleanup_idx > clone_idx, (
            "%s: _reinit_cleanup must run AFTER the clone so a failed clone "
            "can't strand the instance with no .git (#663)" % fname
        )

    def test_k8s_join(self):
        self._assert("join_kubernetes.yml")


class TestJoinGuardSkippedUnderReinit:
    """The '.git already exists' guard must not fire under --reinit, since
    we now keep that .git in place until the clone succeeds."""

    def _guard_when(self, fname):
        for t in flow_tasks(fname):
            fail = t.get("ansible.builtin.fail") or t.get("fail") or {}
            if "GitOps already initialized" in (fail.get("msg") or ""):
                return _when(t)
        raise AssertionError("%s: no '.git already exists' guard found" % fname)

    def test_k8s_join(self):
        w = self._guard_when("join_kubernetes.yml")
        assert "reinit" in w and "not" in w, (
            "k8s join guard must be gated on `not reinit`: %r" % w
        )
