"""Structural tests for `canasta image prune` and the registry GC.

`image prune` was the only command in the tree with neither unit nor
integration coverage, and it is destructive: it garbage-collects the
in-cluster image registry. The GC itself lives in the shared
registry_gc.yml, which `canasta upgrade --purge` also calls, so a
regression there hits two commands.

These pin the guards that make the command safe to run: it refuses a
host with no cluster, the GC no-ops when no registry is deployed, and
the registry is restarted afterward so its in-memory blob cache matches
the pruned store.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
IMAGE_PRUNE = os.path.join(REPO_ROOT, "playbooks", "image_prune.yml")
PURGE_HOST = os.path.join(REPO_ROOT, "playbooks", "_purge_host.yml")
REGISTRY_GC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "registry_gc.yml",
)

# Guards every task that touches the registry. Written as a substring
# match so a reordering or a multi-condition `when:` still counts.
GATE = "_prune_reg.rc == 0"


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


def _cmd(task):
    """The command string of a task, however it is spelled."""
    for key in ("ansible.builtin.command", "ansible.builtin.shell",
                "command", "shell"):
        val = task.get(key)
        if isinstance(val, dict):
            return str(val.get("cmd", ""))
        if isinstance(val, str):
            return val
    return ""


class TestImagePruneRequiresACluster:
    """Running this on a Compose-only host is always an operator
    mistake, so the command says so instead of quietly doing nothing."""

    def test_probes_for_a_cluster(self):
        probe = next(
            (t for t in _load(IMAGE_PRUNE) if "kubectl get nodes" in _cmd(t)),
            None,
        )
        assert probe is not None, "image_prune.yml must probe for a cluster"
        assert probe.get("failed_when") is False, (
            "the probe must not fail the play — its rc is what drives the "
            "friendly error below, so an unguarded probe would abort first "
            "with a raw kubectl error"
        )
        assert probe.get("changed_when") is False

    def test_fails_when_no_cluster_is_present(self):
        fail = next(
            (t for t in _load(IMAGE_PRUNE)
             if "ansible.builtin.fail" in t), None,
        )
        assert fail is not None, (
            "image_prune.yml must fail when the host has no cluster"
        )
        assert "_prune_k8s.rc" in str(fail.get("when", "")), (
            "the failure must be gated on the cluster probe's rc"
        )

    def test_delegates_to_the_shared_gc(self):
        """The GC must not be reimplemented here — upgrade --purge calls
        the same file, and two copies would drift."""
        assert any(
            "registry_gc.yml" in str(t.get("ansible.builtin.include_tasks", ""))
            for t in _load(IMAGE_PRUNE)
        ), "image_prune.yml must include roles/orchestrator/tasks/registry_gc.yml"


class TestUpgradePurgeSkipsInsteadOfFailing:
    """The other caller runs the same probe and must reach the opposite
    conclusion: a Compose host legitimately has no cluster, so purge
    skips the K8s reclaim rather than failing the upgrade."""

    def test_k8s_reclaim_is_skipped_not_fatal(self):
        tasks = _load(PURGE_HOST)
        probe = next((t for t in tasks if "kubectl get nodes" in _cmd(t)), None)
        assert probe is not None
        assert probe.get("failed_when") is False

        gc_block = next(
            (t for t in tasks
             if "registry_gc.yml" in str(
                 t.get("ansible.builtin.include_tasks", ""))),
            None,
        )
        assert gc_block is not None, (
            "_purge_host.yml must call the shared registry GC"
        )
        assert not any(
            "ansible.builtin.fail" in t and "_purge_k8s" in str(t.get("when", ""))
            for t in tasks
        ), (
            "a host with no cluster is normal during upgrade --purge; it "
            "must not fail the run the way image prune does"
        )


class TestRegistryGCIsSafeToCallUnconditionally:
    """The file is documented as safe to call on any cluster, which
    means every registry-touching task has to be gated on the registry
    actually being deployed."""

    def test_probe_allows_inspecting_its_rc(self):
        probe = next(
            (t for t in _load(REGISTRY_GC)
             if "get deploy/registry" in _cmd(t)), None,
        )
        assert probe is not None, "registry_gc.yml must probe for the registry"
        assert probe.get("failed_when") is False
        assert probe.get("register") == "_prune_reg"

    def test_missing_registry_reports_rather_than_fails(self):
        report = next(
            (t for t in _load(REGISTRY_GC)
             if "ansible.builtin.debug" in t
             and "_prune_reg.rc != 0" in str(t.get("when", ""))),
            None,
        )
        assert report is not None, (
            "a cluster with no registry must be reported, not failed — "
            "upgrade --purge calls this on every K8s host"
        )
        assert not any("ansible.builtin.fail" in t for t in _load(REGISTRY_GC)), (
            "registry_gc.yml must not fail; its callers decide what a "
            "missing cluster or registry means"
        )

    def test_every_registry_operation_is_gated(self):
        ungated = []
        for task in _load(REGISTRY_GC):
            name = task.get("name", "")
            is_op = (
                "kubectl" in _cmd(task)
                or "include_tasks" in " ".join(task.keys())
            )
            if not is_op:
                continue
            if task.get("register") == "_prune_reg":
                continue  # the probe itself
            if GATE not in str(task.get("when", "")):
                ungated.append(name)
        assert not ungated, (
            "these tasks touch the registry without checking it is "
            "deployed, so they would run against a cluster that has "
            "none: %s" % ", ".join(ungated)
        )


class TestRegistryGCBehavior:
    def test_collects_untagged_manifests(self):
        """Without --delete-untagged the GC reclaims nothing: repeated
        pushes of the same tag orphan manifests, not just layers."""
        gc = next(
            (t for t in _load(REGISTRY_GC)
             if "garbage-collect" in _cmd(t)), None,
        )
        assert gc is not None, "registry_gc.yml must run the registry GC"
        assert "--delete-untagged" in _cmd(gc)

    def test_restarts_the_registry_after_collecting(self):
        """The running registry caches blob existence in memory; after an
        on-disk GC it serves stale metadata and rejects re-pushes."""
        tasks = _load(REGISTRY_GC)
        names = [t.get("name", "") for t in tasks]
        gc_at = next(i for i, t in enumerate(tasks)
                     if "garbage-collect" in _cmd(t))
        restart_at = next(
            (i for i, t in enumerate(tasks)
             if "rollout restart" in _cmd(t)), None,
        )
        assert restart_at is not None, (
            "the registry must be restarted after the GC; found: %s" % names
        )
        assert restart_at > gc_at, "the restart must follow the GC, not precede it"

    def test_waits_for_the_registry_to_come_back(self):
        """Returning before the rollout finishes would let a following
        push hit a registry that is still terminating."""
        assert any(
            "rollout status" in _cmd(t) for t in _load(REGISTRY_GC)
        ), "registry_gc.yml must wait for the restarted registry"

    def test_refreshes_manifests_before_pruning(self):
        """A registry deployed by an older CLI can lack the Recreate
        strategy the restart depends on, and would deadlock its rollout."""
        tasks = _load(REGISTRY_GC)
        ensure_at = next(
            (i for i, t in enumerate(tasks)
             if "k8s_ensure_registry.yml" in str(
                 t.get("ansible.builtin.include_tasks", ""))),
            None,
        )
        assert ensure_at is not None, (
            "registry_gc.yml must re-apply the registry manifests first"
        )
        gc_at = next(i for i, t in enumerate(tasks)
                     if "garbage-collect" in _cmd(t))
        assert ensure_at < gc_at
