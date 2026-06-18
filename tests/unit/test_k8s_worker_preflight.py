"""Preflight / safety guards for the multi-node K8s worker join and
uninstall paths.

A full two-node worker-join e2e needs two systemd k3s hosts, which a
single CI runner can't provide. These structural tests instead lock in
the preflight and cleanup behavior of the worker-join (k3s_worker.yml),
the shared cp-host resolver (resolve_cp_host_ssh.yml), and the uninstall
(uninstall_k3s.yml) so the operator-facing safety properties can't
regress:

  - a misconfigured `--cp-host` fails with an actionable message instead
    of a censored "non-zero return code" (the join probes the cp's k3s
    before the token fetch);
  - the join token is never logged;
  - an unregistered `--cp-host` is rejected up front;
  - a worker uninstall requires `--cp-host` and removes the worker's Node
    from the control plane so it doesn't linger as NotReady.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
WORKER = os.path.join(REPO_ROOT, "roles", "install", "tasks", "k3s_worker.yml")
UNINSTALL = os.path.join(
    REPO_ROOT, "roles", "install", "tasks", "uninstall_k3s.yml",
)
RESOLVE_CP = os.path.join(
    REPO_ROOT, "roles", "common", "tasks", "resolve_cp_host_ssh.yml",
)


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _load(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _named(tasks, needle):
    """First task whose name contains `needle` (case-insensitive)."""
    for t in tasks:
        if needle.lower() in (t.get("name", "")).lower():
            return t
    return None


class TestWorkerJoinPreflight:
    def test_resolves_cp_host_before_use(self):
        tasks = _load(WORKER)
        inc = _named(tasks, "Resolve cp-host SSH target")
        assert inc is not None, "worker join must resolve --cp-host"
        target = inc.get("ansible.builtin.include_tasks") or \
            inc.get("include_tasks") or ""
        assert "resolve_cp_host_ssh.yml" in target

    def test_probes_cp_k3s_without_aborting(self):
        # The probe itself must not hard-fail (failed_when: false) so the
        # explicit, friendly fail task below can run instead of a raw
        # module error.
        tasks = _load(WORKER)
        probe = _named(tasks, "Probe cp-host for active k3s")
        assert probe is not None, "worker join must probe the cp's k3s"
        assert probe.get("failed_when") is False

    def test_fails_clearly_when_cp_has_no_control_plane(self):
        # This is the anti-silent-failure guard: a misconfigured cp-host
        # must produce an actionable message, not a censored module error.
        tasks = _load(WORKER)
        fail = _named(tasks, "no active k3s control plane")
        assert fail is not None, (
            "worker join must fail with a clear message when the cp-host "
            "has no running k3s control plane"
        )
        assert "ansible.builtin.fail" in fail or "fail" in fail
        msg = (fail.get("ansible.builtin.fail") or fail.get("fail") or {}) \
            .get("msg", "")
        assert "k8s-cp" in msg, (
            "the failure message should point the operator at "
            "'canasta install k8s-cp'"
        )
        # Gated on the probe result (rc != active).
        when = fail.get("when", "")
        assert "_cp_k3s_active" in when

    def test_join_token_is_not_logged(self):
        tasks = _load(WORKER)
        fetch = _named(tasks, "Fetch k3s join token")
        assert fetch is not None
        assert fetch.get("no_log") is True, (
            "the join-token fetch must set no_log: true"
        )

    def test_validates_fetched_cluster_info(self):
        tasks = _load(WORKER)
        validate = _named(tasks, "Validate fetched values")
        assert validate is not None, (
            "worker join must fail clearly when token/IP discovery "
            "returns empty"
        )


class TestCpHostResolver:
    def test_rejects_unregistered_cp_host(self):
        tasks = _load(RESOLVE_CP)
        fail = _named(tasks, "not registered")
        assert fail is not None, (
            "resolver must reject an unregistered --cp-host"
        )
        when = fail.get("when", "")
        assert "_cp_host_entry" in when

    def test_handles_bare_host_and_user_at_host(self):
        # The SSH-target composition must not assume an ansible_user key
        # (entries added via `host add --ssh host` omit it).
        tasks = _load(RESOLVE_CP)
        compose = _named(tasks, "Compose cp-host SSH target")
        assert compose is not None
        expr = str(
            (compose.get("ansible.builtin.set_fact")
             or compose.get("set_fact") or {})
        )
        assert "ansible_user" in expr and "ansible_host" in expr


class TestWorkerUninstall:
    def test_handles_both_server_and_agent_scripts(self):
        tasks = _load(UNINSTALL)
        assert _named(tasks, "k3s server uninstall script") is not None
        assert _named(tasks, "k3s agent uninstall script") is not None

    def test_worker_uninstall_requires_cp_host(self):
        tasks = _load(UNINSTALL)
        req = _named(tasks, "Require --cp-host for worker uninstalls")
        assert req is not None, (
            "a worker uninstall must require --cp-host so the Node can be "
            "removed from the control plane"
        )
        when = req.get("when", [])
        when_s = " ".join(when) if isinstance(when, list) else str(when)
        assert "worker" in when_s and "cp_host" in when_s

    def test_deletes_worker_node_from_control_plane(self):
        tasks = _load(UNINSTALL)
        delete = _named(tasks, "Delete Node from control plane")
        assert delete is not None, (
            "worker uninstall must remove the Node from the cp so it "
            "doesn't linger as NotReady"
        )
        argv = (delete.get("ansible.builtin.command")
                or delete.get("command") or {}).get("argv", [])
        joined = " ".join(str(a) for a in argv)
        assert "kubectl delete node" in joined
        assert "--ignore-not-found" in joined
