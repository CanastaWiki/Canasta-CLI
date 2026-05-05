"""start, stop, restart, scale — lifecycle commands."""

import os
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


# _k8s_namespace lives in _helpers.py — _exec_in_container (a shared
# helper) needs it too, so it can't be lifecycle-only.
_k8s_namespace = _helpers._k8s_namespace


def _run_kubectl(kubectl_args, timeout=30):
    """Run a kubectl command. Returns exit code."""
    cmd = ["kubectl"] + kubectl_args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.returncode != 0 and result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    except (subprocess.TimeoutExpired, OSError) as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1


def _k8s_stop(instance_id):
    """Stop a K8s instance: suspend Argo CD sync, scale everything to 0."""
    ns = _k8s_namespace(instance_id)

    result = subprocess.run(
        ["kubectl", "get", "application", "canasta-%s" % instance_id,
         "-n", "argocd", "-o", "jsonpath={.metadata.name}"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        _run_kubectl([
            "patch", "application", "canasta-%s" % instance_id,
            "-n", "argocd", "--type", "merge",
            "-p", '{"spec":{"syncPolicy":null}}',
        ])

    _run_kubectl(["scale", "deployment", "--all", "--replicas=0", "-n", ns])

    # External-DB instances with Elasticsearch disabled have no
    # StatefulSets; 'kubectl scale --all' errors with "no objects
    # passed to scale". Check first and skip if none exist.
    sts_check = subprocess.run(
        ["kubectl", "get", "statefulset", "-n", ns, "-o", "name"],
        capture_output=True, text=True, timeout=10,
    )
    if sts_check.returncode == 0 and sts_check.stdout.strip():
        _run_kubectl(["scale", "statefulset", "--all", "--replicas=0", "-n", ns])
    return 0


@register("start")
def cmd_start(args):
    inst_id, inst = _helpers._resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        # K8s start requires chart copy + helm deploy + config sync;
        # these are multi-step controller-to-remote operations that
        # need Ansible.
        return _helpers.FALLBACK
    _helpers._sync_compose_profiles(inst)
    rc = _helpers._run_compose(inst_id, inst, ["up", "-d"])
    if rc != 0:
        _helpers._dump_compose_failure(inst)
    return rc


@register("stop")
def cmd_stop(args):
    inst_id, inst = _helpers._resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        return _k8s_stop(inst_id)
    return _helpers._run_compose(inst_id, inst, ["down"])


@register("restart")
def cmd_restart(args):
    inst_id, inst = _helpers._resolve_instance(args)
    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        # K8s restart needs Ansible for the start half (helm deploy).
        return _helpers.FALLBACK
    rc = _helpers._run_compose(inst_id, inst, ["down"])
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
    if _helpers._is_localhost(host):
        argv = ["bash", "-c", helm_cmd]
    else:
        target = _helpers._resolve_ssh_target(host)
        argv = ["ssh"] + _helpers._ssh_args() + [target, helm_cmd]
    try:
        rc = subprocess.call(argv)
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
