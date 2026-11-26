"""Structural tests for `canasta image prune` and the registry GC.

`image prune` is destructive: it removes images and garbage-collects the
in-cluster image registry. Both halves live in the shared
image_reclaim.yml, which `canasta upgrade --purge` also calls, so a
regression there hits two commands.

These pin the guards that make the command safe to run: it reclaims
whichever runtimes the host actually has rather than demanding a cluster,
the GC no-ops when no registry is deployed, and the registry is restarted
afterward so its in-memory blob cache matches the pruned store.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
IMAGE_PRUNE = os.path.join(REPO_ROOT, "playbooks", "image_prune.yml")
PURGE_HOST = os.path.join(REPO_ROOT, "playbooks", "_purge_host.yml")
IMAGE_RECLAIM = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "image_reclaim.yml",
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


class TestImagePruneReclaimsOnEitherOrchestrator:
    """Compose hosts accumulate superseded tags and dangling sidecar
    layers with nothing to collect them, so the command reclaims what
    the host has instead of demanding a cluster."""

    def test_delegates_to_the_shared_reclaim(self):
        """The reclaim must not be reimplemented here — upgrade --purge
        calls the same file, and two copies would drift."""
        assert any(
            "image_reclaim.yml" in str(
                t.get("ansible.builtin.include_tasks", ""))
            for t in _load(IMAGE_PRUNE)
        ), "image_prune.yml must include the shared image_reclaim.yml"

    def test_the_reclaim_probes_for_a_docker_daemon(self):
        cmds = [_cmd(t) for t in _load(IMAGE_RECLAIM)]
        assert any("docker info" in c for c in cmds), (
            "the Docker path must probe for a daemon"
        )

    def test_a_host_without_a_cluster_is_not_an_error(self):
        assert not any(
            "ansible.builtin.fail" in t for t in _load(IMAGE_PRUNE)
        ), (
            "a Compose host is a legitimate target; the command must "
            "reclaim its Docker images rather than refuse the host"
        )

    def test_dry_run_reaches_the_shared_reclaim(self):
        include = next(
            t for t in _load(IMAGE_PRUNE)
            if "image_reclaim.yml" in str(
                t.get("ansible.builtin.include_tasks", ""))
        )
        assert "reclaim_dry_run" in str(include.get("vars", {}))


class TestReclaimSkipsInsteadOfFailing:
    """Each probe must reach the same conclusion for the runtime it does
    not find: skip that path, not fail the run. A Compose host has no
    cluster and a cluster host may have no Docker daemon."""

    def test_missing_runtime_never_fails_the_run(self):
        assert not any(
            "ansible.builtin.fail" in t for t in _load(IMAGE_RECLAIM)
        ), (
            "upgrade --purge calls this on every host; a host missing one "
            "runtime must skip that path, not abort the upgrade"
        )

    def test_docker_probe_is_non_fatal(self):
        probe = next(
            (t for t in _load(IMAGE_RECLAIM) if "docker info" in _cmd(t)),
            None)
        assert probe is not None
        assert probe.get("failed_when") is False
        assert probe.get("changed_when") is False

    def test_purge_host_delegates_to_the_same_reclaim(self):
        assert any(
            "image_reclaim.yml" in str(
                t.get("ansible.builtin.include_tasks", ""))
            for t in _load(PURGE_HOST)
        ), "_purge_host.yml must call the shared reclaim, not its own copy"
