"""start, stop, restart, scale — lifecycle commands."""

import sys

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
    # Reconcile COMPOSE_PROFILES before `down`: docker compose down only tears
    # down services in the active profiles, so a drifted profile set leaves a
    # running container as an unmanaged orphan. Sync first so `down` covers the
    # full intended set.
    _helpers._sync_compose_profiles(inst)
    # --remove-orphans sweeps a sidecar container left over from a sidecar
    # that was just removed: sidecars.yaml is now empty (so we take this
    # non-sidecar path), but its docker-compose.sidecars.yml entry and
    # running container still linger and a plain `down` would not touch them.
    rc = _helpers._run_compose(
        inst_id, inst, ["down", "--remove-orphans"], quiet=True)
    if rc == 0:
        print("Instance '%s' stopped." % inst_id)
    return rc


_SCALE_SUPPORTED_COMPONENTS = ("web",)


@register("scale")
def cmd_scale(args):
    inst_id, inst = _helpers._resolve_instance(args)
    component = (getattr(args, "component", None) or "web").lower()
    if component not in _SCALE_SUPPORTED_COMPONENTS:
        print(
            "Error: only 'web' supports replica scaling. "
            "Component '%s' is not supported." % component,
            file=sys.stderr,
        )
        return 1

    replicas_raw = getattr(args, "replicas", None)
    if replicas_raw is None:
        print("Error: --replicas is required", file=sys.stderr)
        return 1
    try:
        replicas = int(replicas_raw)
    except (TypeError, ValueError):
        print(
            "Error: --replicas must be an integer (got %r)" % replicas_raw,
            file=sys.stderr,
        )
        return 1
    if replicas < 1:
        print("Error: --replicas must be ≥ 1", file=sys.stderr)
        return 1

    # Persisting the count and deploying it run in Ansible, so scale
    # shares the chart sync, domain derivation and rollout-failure
    # reporting of every other Helm deploy.
    return _helpers.FALLBACK
