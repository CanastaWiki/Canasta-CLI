"""rebuild — rebuild buildable services and restart."""

import json
import subprocess
import sys

from . import _helpers
from ._helpers import register


def _list_buildable_services(inst, include_sidecars=False):
    """Return service names with a build: directive in the merged compose config.

    Uses compose config --format json so the compose tool itself
    handles the main + override + dev file merging — no need to
    parse YAML ourselves.
    """
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")
    devmode = inst.get("devMode", False)
    file_args = _helpers._compose_file_args(path, host, devmode, include_sidecars)
    compose_cmd = _helpers._resolve_compose_cmd(inst)

    if _helpers._is_localhost(host):
        try:
            result = subprocess.run(
                compose_cmd + file_args + ["config", "--format", "json"],
                cwd=path, capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error querying compose config: %s" % e, file=sys.stderr)
            return []
        if result.returncode != 0:
            print(
                "Error: compose config failed: %s"
                % (result.stderr or "").strip(),
                file=sys.stderr,
            )
            return []
        stdout = result.stdout
    else:
        compose_str = " ".join(compose_cmd)
        rc, stdout = _helpers._ssh_run(
            host,
            "cd %s && %s %s config --format json" % (
                _helpers._shell_quote(path), compose_str, " ".join(file_args),
            ),
        )
        if rc != 0:
            print("Error: compose config failed on %s" % host, file=sys.stderr)
            return []

    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        print("Error: docker compose config returned invalid JSON", file=sys.stderr)
        return []

    services = data.get("services", {}) or {}
    return [
        name for name, svc in services.items()
        if isinstance(svc, dict) and "build" in svc
    ]


@register("rebuild")
def cmd_rebuild(args):
    inst_id, inst = _helpers._resolve_instance(args)

    if inst.get("orchestrator", "compose") in ("kubernetes", "k8s"):
        print(
            "Error: 'canasta rebuild' is only supported for Compose "
            "instances. For Kubernetes instances with a custom image, "
            "rebuild and push the image to a registry, then run "
            "'canasta upgrade'.",
            file=sys.stderr,
        )
        return 1

    # Layer the rendered docker-compose.sidecars.yml only when
    # config/sidecars.yaml declares sidecars — the same file set the
    # Ansible stop/start path uses. Without it, `down`/`up -d` run with
    # an incomplete file set and tear down the sidecar containers.
    has_sidecars = _helpers._instance_has_sidecars(inst)

    services = _list_buildable_services(inst, include_sidecars=has_sidecars)
    if not services:
        print(
            "No services have a build: directive — nothing to rebuild. "
            "Add a docker-compose.override.yml with a build: section "
            "to layer a custom image."
        )
        return 0

    build_argv = ["build"]
    if getattr(args, "no_cache", False):
        build_argv.append("--no-cache")
    build_argv.extend(services)

    print("Rebuilding: %s" % ", ".join(services))
    rc = _helpers._run_compose(
        inst_id, inst, build_argv, include_sidecars=has_sidecars)
    if rc != 0:
        return rc

    if getattr(args, "no_restart", False):
        print(
            "Build complete. Skipping restart (--no-restart). "
            "Run 'canasta restart -i %s' to pick up the new image." % inst_id
        )
        return 0

    print("Restarting containers to pick up the rebuilt image...")
    rc = _helpers._run_compose(
        inst_id, inst, ["down"], include_sidecars=has_sidecars)
    if rc != 0:
        return rc
    _helpers._sync_compose_profiles(inst)
    rc = _helpers._run_compose(
        inst_id, inst, ["up", "-d"], include_sidecars=has_sidecars)
    if rc != 0:
        _helpers._dump_compose_failure(inst, include_sidecars=has_sidecars)
    return rc
