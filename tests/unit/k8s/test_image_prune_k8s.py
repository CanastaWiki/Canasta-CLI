"""The Kubernetes half of `canasta image prune` and the in-cluster registry GC.

The shared reclaim probes for a cluster without failing and reaches the
registry GC. The GC no-ops when no registry is deployed, gates every
registry operation on the registry being present, collects untagged
manifests, and restarts the registry afterward so its in-memory blob cache
matches the pruned store.
"""

import os

from test_image_prune import IMAGE_RECLAIM, REPO_ROOT, _cmd, _load


REGISTRY_GC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "registry_gc.yml",
)

# Guards every task that touches the registry. Written as a substring
# match so a reordering or a multi-condition `when:` still counts.
GATE = "_prune_reg.rc == 0"


class TestImagePruneReclaimsOnEitherOrchestrator:
    def test_the_reclaim_probes_for_a_cluster(self):
        cmds = [_cmd(t) for t in _load(IMAGE_RECLAIM)]
        assert any("kubectl get nodes" in c for c in cmds), (
            "the Kubernetes path must probe for a cluster"
        )


class TestReclaimSkipsInsteadOfFailing:
    def test_cluster_probe_is_non_fatal(self):
        probe = next(
            (t for t in _load(IMAGE_RECLAIM)
             if "kubectl get nodes" in _cmd(t)), None)
        assert probe is not None
        assert probe.get("failed_when") is False
        assert probe.get("changed_when") is False

    def test_the_shared_gc_is_still_reached(self):
        assert any(
            "registry_gc.yml" in str(
                t.get("ansible.builtin.include_tasks", ""))
            for t in _load(IMAGE_RECLAIM)
        ), "the Kubernetes path must call the shared registry GC"


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
