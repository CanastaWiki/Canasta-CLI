# -*- coding: utf-8 -*-
"""Translate a legacy docker-compose.override.yml into the orchestrator-agnostic
sidecar schema (config/sidecars.yaml).

Pure functions shared by the canasta_sidecar_migrate module and its tests. The
inverse of canasta_sidecar_render's Compose path: a Compose service becomes a
sidecar declaration. Core-stack services (web/db/caddy/…) are overrides, not
sidecars, and are left in the override. A candidate service is migrated only if
EVERY field can be modelled; otherwise it is left in the override and reported,
so nothing is silently half-migrated.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os

try:
    from ansible.module_utils.canasta_validate import RESERVED_SIDECAR_NAMES
except ImportError:  # unit tests add module_utils to sys.path directly
    from canasta_validate import RESERVED_SIDECAR_NAMES

# A named Compose volume carries no size; the agnostic persistent volume needs
# one for the k8s PVC, so migration assumes this and reports it.
DEFAULT_VOLUME_SIZE = "1Gi"

# Service keys the translation understands. Anything else makes the service
# unmodellable (left in the override), except the benign keys below.
_HANDLED = {
    "image", "build", "command", "environment", "expose", "ports",
    "volumes", "depends_on", "healthcheck", "deploy",
}
_BENIGN = {"restart", "container_name", "hostname", "labels"}


def _env_to_map(environment):
    out = {}
    if isinstance(environment, dict):
        for key, value in environment.items():
            out[key] = "" if value is None else str(value)
    else:
        for item in environment or []:
            text = str(item)
            if "=" in text:
                key, value = text.split("=", 1)
                out[key] = value
            else:
                out[text] = ""
    return out


def _ports_to_list(svc):
    out = []
    for entry in svc.get("expose", []) or []:
        out.append(int(str(entry)))
    for entry in svc.get("ports", []) or []:
        # HOST:CONTAINER[/proto] or CONTAINER — take the container port.
        container = str(entry).split("/")[0].split(":")[-1]
        port = int(container)
        if port not in out:
            out.append(port)
    return out


def _volumes(svc, reasons, assumptions, name):
    out = []
    files = []
    for entry in svc.get("volumes", []) or []:
        if isinstance(entry, dict):
            reasons.append("long-form volume syntax")
            continue
        parts = str(entry).split(":")
        if len(parts) == 1:  # anonymous ephemeral
            mount = parts[0]
            out.append({"name": os.path.basename(mount.rstrip("/")) or "data",
                        "mountPath": mount, "persistent": False})
            continue
        source, mount = parts[0], parts[1]
        mode = parts[2] if len(parts) > 2 else "rw"
        if source.startswith((".", "/", "~")):  # bind mount -> files
            files.append({"source": source.lstrip("./"), "mountPath": mount,
                          "readOnly": "ro" in mode})
        else:  # named volume -> persistent (size assumed)
            out.append({"name": source, "mountPath": mount,
                        "persistent": True, "size": DEFAULT_VOLUME_SIZE})
            assumptions.append(
                "%s: assumed size %s for volume '%s'"
                % (name, DEFAULT_VOLUME_SIZE, source))
    return out, files


def _healthcheck(hc, reasons):
    test = hc.get("test")
    if isinstance(test, list) and test:
        kind = test[0]
        if kind == "CMD":
            return {"command": list(test[1:])}
        if kind == "CMD-SHELL":
            return {"command": ["sh", "-c", test[1]]}
        if kind == "NONE":
            return None
    if isinstance(test, str):
        return {"command": ["sh", "-c", test]}
    reasons.append("unrecognized healthcheck")
    return None


def _build(svc, reasons):
    build = svc.get("build")
    if isinstance(build, str):
        return build
    if isinstance(build, dict):
        if build.get("dockerfile") and build["dockerfile"] != "Dockerfile":
            reasons.append("build with a custom dockerfile")
            return None
        if build.get("context"):
            return build["context"]
        reasons.append("build without a context")
    return None


def translate_service(name, svc):
    """Translate one Compose service into a sidecar dict.

    Returns (sidecar | None, reasons, assumptions). sidecar is None when the
    service can't be fully modelled (reasons explains why)."""
    reasons = []
    assumptions = []
    svc = svc or {}

    for key in svc:
        if key not in _HANDLED and key not in _BENIGN:
            reasons.append("unsupported field '%s'" % key)
    deploy = svc.get("deploy") or {}
    for key in deploy:
        if key != "resources":
            reasons.append("deploy.%s" % key)

    sidecar = {"name": name}
    has_build = "build" in svc
    if has_build:
        build = _build(svc, reasons)
        if build is not None:
            sidecar["build"] = build
    elif svc.get("image"):
        sidecar["image"] = svc["image"]
    else:
        reasons.append("no image or build")

    if svc.get("command"):
        sidecar["command"] = svc["command"]
    if svc.get("environment"):
        sidecar["env"] = _env_to_map(svc["environment"])
    ports = _ports_to_list(svc)
    if ports:
        sidecar["ports"] = ports
    volumes, files = _volumes(svc, reasons, assumptions, name)
    if volumes:
        sidecar["volumes"] = volumes
    if files:
        sidecar["files"] = files
    if svc.get("depends_on"):
        deps = svc["depends_on"]
        sidecar["depends_on"] = list(deps.keys()) if isinstance(deps, dict) \
            else list(deps)
    if svc.get("healthcheck"):
        hc = _healthcheck(svc["healthcheck"], reasons)
        if hc:
            sidecar["healthcheck"] = hc
    limits = (deploy.get("resources") or {}).get("limits") or {}
    resources = {}
    if limits.get("memory"):
        resources["memory"] = limits["memory"]
    if limits.get("cpus"):
        resources["cpu"] = limits["cpus"]
    if resources:
        sidecar["resources"] = resources

    if reasons:
        return None, reasons, assumptions
    return sidecar, reasons, assumptions


def plan_migration(override, existing_names=None):
    """Plan migrating an override dict's services.

    `existing_names` are sidecars already in config/sidecars.yaml; a service
    that collides with one is skipped (left in the override) rather than
    silently overwriting. Returns a dict: sidecars (migratable list), migrated
    (names), skipped ([{name, reasons}]), assumptions ([str]), and
    remaining_override (the override with migrated services — and their
    now-unreferenced top-level volumes — removed)."""
    existing_names = set(existing_names or [])
    services = (override or {}).get("services") or {}
    sidecars = []
    migrated = []
    skipped = []
    assumptions = []
    for name, svc in services.items():
        if name in RESERVED_SIDECAR_NAMES:
            continue  # a core-stack override, not a sidecar
        if name in existing_names:
            skipped.append({"name": name,
                            "reasons": ["already declared in config/sidecars.yaml"]})
            continue
        sidecar, reasons, notes = translate_service(name, svc)
        if sidecar is None:
            skipped.append({"name": name, "reasons": reasons})
        else:
            sidecars.append(sidecar)
            migrated.append(name)
            assumptions.extend(notes)

    remaining = dict(override or {})
    if migrated:
        kept_services = {n: s for n, s in services.items()
                         if n not in migrated}
        remaining["services"] = kept_services
        # Drop top-level volumes no surviving service references.
        top_volumes = (override or {}).get("volumes") or {}
        if top_volumes:
            still_used = set()
            for svc in kept_services.values():
                for entry in (svc or {}).get("volumes", []) or []:
                    if isinstance(entry, str) and ":" in entry:
                        src = entry.split(":")[0]
                        if not src.startswith((".", "/", "~")):
                            still_used.add(src)
            kept_volumes = {k: v for k, v in top_volumes.items()
                            if k in still_used}
            if kept_volumes:
                remaining["volumes"] = kept_volumes
            else:
                remaining.pop("volumes", None)

    return {
        "sidecars": sidecars,
        "migrated": migrated,
        "skipped": skipped,
        "assumptions": assumptions,
        "remaining_override": remaining,
    }
