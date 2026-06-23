"""start, stop, restart, scale — lifecycle commands."""

import os
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


@register("start")
def cmd_start(args):
    inst_id, inst = _helpers._resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        # K8s start requires chart copy + helm deploy + config sync;
        # these are multi-step controller-to-remote operations that
        # need Ansible.
        return _helpers.FALLBACK
    if _helpers._instance_has_sidecars(inst):
        return _helpers.FALLBACK  # Ansible renders + layers the sidecars.
    _helpers._sync_compose_profiles(inst)
    rc = _helpers._run_compose(inst_id, inst, ["up", "-d"])
    if rc != 0:
        _helpers._dump_compose_failure(inst)
    return rc


@register("stop")
def cmd_stop(args):
    inst_id, inst = _helpers._resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        # K8s stop suspends Argo CD sync and scales the instance's
        # workloads to 0 — kubectl must run on the instance's host
        # (where the cluster kubeconfig lives), not the controller.
        # Ansible resolves the host and switches the connection.
        return _helpers.FALLBACK
    if _helpers._instance_has_sidecars(inst):
        return _helpers.FALLBACK  # Ansible includes the sidecar -f layer.
    # --remove-orphans sweeps a sidecar container left over from a sidecar
    # that was just removed: sidecars.yaml is now empty (so we take this
    # non-sidecar path), but its docker-compose.sidecars.yml entry and
    # running container still linger and a plain `down` would not touch them.
    return _helpers._run_compose(inst_id, inst, ["down", "--remove-orphans"])


@register("restart")
def cmd_restart(args):
    inst_id, inst = _helpers._resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        # K8s restart needs Ansible for the start half (helm deploy).
        return _helpers.FALLBACK
    if _helpers._instance_has_sidecars(inst):
        return _helpers.FALLBACK  # Ansible renders + layers the sidecars.
    # --remove-orphans sweeps a sidecar container left over from a sidecar
    # that was just removed (sidecars.yaml is empty so we take this path, but
    # its docker-compose.sidecars.yml entry and container still linger).
    rc = _helpers._run_compose(inst_id, inst, ["down", "--remove-orphans"])
    if rc != 0:
        return rc
    _helpers._sync_compose_profiles(inst)
    rc = _helpers._run_compose(inst_id, inst, ["up", "-d"])
    if rc != 0:
        _helpers._dump_compose_failure(inst)
    return rc

_SCALE_SUPPORTED_COMPONENTS = ("web",)


@register("scale")
def cmd_scale(args):
    inst_id, inst = _helpers._resolve_instance(args)
    orchestrator = inst.get("orchestrator", "compose")
    if orchestrator not in ("kubernetes", "k8s"):
        # Kubernetes-only by design: Compose runs all replicas on the
        # same host with no built-in cross-replica load balancing, so
        # the spread-across-nodes / horizontal-capacity model that
        # motivates this command doesn't apply. Bump PHP-FPM worker
        # limits inside the image instead.
        print(
            "Error: `canasta scale` is Kubernetes-only by design. "
            "On Compose, raise PHP-FPM worker limits inside the web "
            "image rather than running multiple replicas of the same "
            "container.",
            file=sys.stderr,
        )
        return 1

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

    path = inst.get("path", "")
    host = inst.get("host") or "localhost"
    if not path:
        print("Error: instance '%s' has no path" % inst_id, file=sys.stderr)
        return 1

    values_path = os.path.join(path, "values.yaml")
    content = _helpers._read_remote_or_local_file(values_path, host)
    if content is None:
        return 1

    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as e:
        print("Error: %s is not valid YAML: %s" % (values_path, e), file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("Error: %s does not parse as a mapping" % values_path, file=sys.stderr)
        return 1

    section = data.setdefault(component, {}) or {}
    if not isinstance(section, dict):
        print(
            "Error: %s.%s is not a mapping; refusing to overwrite "
            "scalar with a replicaCount key." % (values_path, component),
            file=sys.stderr,
        )
        return 1
    current = section.get("replicaCount")
    if current == replicas:
        print(
            "%s.replicaCount is already %d; nothing to do." % (component, replicas)
        )
        return 0
    section["replicaCount"] = replicas
    data[component] = section

    new_content = yaml.safe_dump(
        data, default_flow_style=False, sort_keys=False,
    )
    if not _helpers._write_remote_or_local_file(values_path, host, new_content):
        return 1

    # Apply via helm upgrade. Mirror the args helm_deploy.yml uses so
    # the result matches what `canasta start` / `canasta restart`
    # would render — including values-configdata.yaml when present.
    namespace = "canasta-%s" % inst_id
    chart = os.path.join(path, "_chart")
    configdata = os.path.join(path, "values-configdata.yaml")
    cmd_parts = [
        "helm upgrade --install canasta-%s %s" % (inst_id, _helpers._shell_quote(chart)),
        "--namespace %s --create-namespace" % namespace,
        "-f %s" % _helpers._shell_quote(values_path),
    ]
    # Probe for the optional configdata file the same way helm_deploy.yml does.
    if _helpers._is_localhost(host):
        has_configdata = os.path.isfile(configdata)
    else:
        rc, _ = _helpers._ssh_run(
            host, "test -f %s" % _helpers._shell_quote(configdata),
        )
        has_configdata = (rc == 0)
    if has_configdata:
        cmd_parts.append("-f %s" % _helpers._shell_quote(configdata))
    cmd_parts.extend(["--reset-values", "--wait", "--timeout 10m"])
    helm_cmd = " ".join(cmd_parts)

    print("Scaling %s to %d replica(s)…" % (component, replicas))
    # Don't go through _helpers._ssh_run for the helm call — its 30s timeout is
    # short by an order of magnitude vs helm's own `--wait --timeout
    # 10m`, which would falsely report failure on a slow rollout.
    # Stream the helm output through to the operator so they can see
    # progress / diagnose stuck rollouts.
    try:
        if _helpers._is_localhost(host):
            rc = subprocess.call(["bash", "-c", helm_cmd])
        else:
            target = _helpers._resolve_ssh_target(host)
            argv = ["ssh"] + _helpers._ssh_args() + [target, helm_cmd]
            # helm upgrade --install is idempotent; retry on an ssh
            # connection-level failure (exit 255) so a transient
            # controller-to-target reset during the up-to-10m rollout
            # doesn't abort a deploy that may still be progressing.
            rc = _helpers._retry_on_ssh_reset(lambda: subprocess.call(argv))
    except OSError as e:
        print("Error running helm upgrade: %s" % e, file=sys.stderr)
        return 1
    if rc != 0:
        print(
            "Error: helm upgrade failed; values.yaml is updated but "
            "the deployment may not match. Re-run after fixing the "
            "underlying error.",
            file=sys.stderr,
        )
        return rc

    print(
        "Scaled %s to %d replica(s). Persisted to %s."
        % (component, replicas, values_path)
    )
    return 0
