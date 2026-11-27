"""start, stop, restart, scale — lifecycle commands."""

import os
import sys

import canasta_edge_render
import canasta_edge_writable

from . import _helpers
from ._helpers import register


@register("start")
def cmd_start(args):
    # Ansible starts every instance: K8s needs its chart deployed and
    # config synced, and every Compose instance's place behind the HAProxy
    # edge is rendered there.
    _helpers._resolve_instance(args)
    return _helpers.FALLBACK


@register("stop")
def cmd_stop(args):
    inst_id, inst = _helpers._resolve_instance(args)
    if _helpers._is_k8s(inst):
        # K8s stop suspends Argo CD sync and scales the instance's
        # workloads to 0 — kubectl must run on the instance's host
        # (where the cluster kubeconfig lives), not the controller.
        # Ansible resolves the host and switches the connection.
        return _helpers.FALLBACK
    if _helpers._instance_has_sidecars(inst):
        return _helpers.FALLBACK  # Ansible includes the sidecar -f layer.
    if "podman" in _helpers._resolve_inspect_cmd(inst):
        return _helpers.FALLBACK  # Ansible disables the instance's boot unit.
    if not _helpers._is_localhost(inst.get("host") or "localhost"):
        return _helpers.FALLBACK  # Ansible checks the edge on the instance's host.
    # The start that follows a stop syncs the edge; refuse while the instance
    # is still running rather than fail with it stopped.
    try:
        canasta_edge_writable.check_writable(
            os.path.join(_helpers._get_config_dir(), "edge"), inst.get("path", ""))
    except canasta_edge_render.EdgeError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1
    # Reconcile COMPOSE_PROFILES before `down`: docker compose down only tears
    # down services in the active profiles, so a drifted profile set leaves a
    # running container as an unmanaged orphan. Sync first so `down` covers the
    # full intended set.
    _helpers._sync_compose_profiles(inst)
    was_up = _helpers._compose_containers_up(inst)
    # --remove-orphans sweeps a sidecar container left over from a sidecar
    # that was just removed: sidecars.yaml is now empty (so we take this
    # non-sidecar path), but its docker-compose.sidecars.yml entry and
    # running container still linger and a plain `down` would not touch them.
    rc = _helpers._run_compose(
        inst_id, inst, ["down", "--remove-orphans"], quiet=True)
    if rc == 0 and not was_up:
        print("Instance '%s' is already stopped." % inst_id)
    elif rc == 0:
        print("Instance '%s' stopped." % inst_id)
    return rc
