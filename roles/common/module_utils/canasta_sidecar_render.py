# -*- coding: utf-8 -*-
"""Render an instance's sidecars (config/sidecars.yaml) to runtime artifacts.

Pure functions shared by the canasta_render_sidecars module and its unit
tests. One declaration renders to either a Docker Compose override layer
(`docker-compose.sidecars.yml`) or Helm values (`values-sidecars.yaml`).

`${VAR:-default}` interpolation: Compose interpolates these itself from the
instance `.env`, so the Compose render passes them through unchanged. k8s
has no interpolation, so the k8s render resolves them at render time against
the same `.env`, keeping the value identical on both orchestrators.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import difflib
import os
import re

from ansible.module_utils.canasta_validate import validate_sidecar_name

_ENV_REF = re.compile(r"\$\{([A-Za-z_]\w*)(:-|-)?([^}]*)\}")


def resolve_env_value(value, env):
    """Resolve ${VAR}, ${VAR:-default}, ${VAR-default} against an env dict."""
    if not isinstance(value, str):
        return value

    def repl(match):
        name, op, default = match.group(1), match.group(2), match.group(3)
        val = env.get(name)
        if op == ":-":          # unset OR empty -> default
            return val if val else default
        if op == "-":           # unset -> default (empty kept)
            return val if val is not None else default
        return val if val is not None else ""

    return _ENV_REF.sub(repl, value)


# --- Host access ----------------------------------------------------------- #
# config/sidecars.yaml is read on every start, whoever wrote it, and its paths
# become Compose bind mounts, build contexts, and (k8s) inlined file contents.
# Keep every host path inside the instance and every mount a container path.

_VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def _inside(instance_path, relative):
    """True if `relative` is a relative path that resolves (following
    symlinks) inside instance_path."""
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        return False
    if any(c in relative for c in ":\n\r\0") or relative.startswith("~"):
        return False
    base = os.path.realpath(instance_path)
    target = os.path.realpath(os.path.join(base, relative))
    return target == base or target.startswith(base + os.sep)


def _container_path(path):
    return (isinstance(path, str) and path.startswith("/")
            and not any(c in path for c in ":\n\r\0"))


def validate_host_access(sidecars, instance_path):
    """Return None if no sidecar reaches outside the instance, else an error."""
    for sidecar in sidecars or []:
        name = sidecar.get("name", "?") if isinstance(sidecar, dict) else "?"
        if not isinstance(sidecar, dict):
            return "each sidecar must be a mapping"
        for volume in sidecar.get("volumes") or []:
            if not _container_path((volume or {}).get("mountPath")):
                return ("sidecar '%s': volume mountPath must be an absolute "
                        "container path without ':'" % name)
            if volume.get("persistent") and not _VOLUME_NAME.match(
                    str(volume.get("name", ""))):
                return ("sidecar '%s': persistent volume name must be letters, "
                        "digits, and _ . -" % name)
        for fileref in sidecar.get("files") or []:
            if not _container_path((fileref or {}).get("mountPath")):
                return ("sidecar '%s': file mountPath must be an absolute "
                        "container path without ':'" % name)
            if not _inside(instance_path, fileref.get("source")):
                return ("sidecar '%s': file source must be a relative path "
                        "inside the instance directory" % name)
        build = sidecar.get("build")
        if build:
            context = build.get("context", ".") if isinstance(build, dict) else build
            if not _inside(instance_path, context):
                return ("sidecar '%s': build context must be a relative path "
                        "inside the instance directory" % name)
            dockerfile = build.get("dockerfile") if isinstance(build, dict) else None
            if dockerfile and not _inside(instance_path,
                                          os.path.join(context, dockerfile)):
                return ("sidecar '%s': build dockerfile must be inside the "
                        "instance directory" % name)
    return None


# --- Declaration shape ----------------------------------------------------- #
# Values below are copied into the Compose override and interpolated into the
# Helm chart, so only known build options pass through and every name or
# quantity is held to a shape that cannot carry extra YAML or build options.

_SIDECAR_KEYS = (
    "name", "image", "build", "command", "env", "envSecret", "envPrivate",
    "ports", "volumes", "files", "depends_on", "healthcheck", "resources",
)
_BUILD_KEYS = ("args", "context", "dockerfile")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")
_QUANTITY = re.compile(
    r"^[0-9]+(\.[0-9]+)?([KMGTPE]i|[bkmgtpeKMGTPE][bB]?)?\Z")


def _scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def _quantity(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value >= 0
    return isinstance(value, str) and bool(_QUANTITY.match(value))


def validate_spec(sidecars):
    """Return None if every sidecar's keys, names, env keys, build options,
    and resource quantities are well-formed, else an error."""
    for sidecar in sidecars or []:
        if not isinstance(sidecar, dict):
            return "each sidecar must be a mapping"
        name = sidecar.get("name")
        if not isinstance(name, str):
            return "each sidecar must have a name"
        error = validate_sidecar_name(name)
        if error:
            return error
        for key in sidecar:
            if key not in _SIDECAR_KEYS:
                close = difflib.get_close_matches(
                    str(key), _SIDECAR_KEYS, n=1, cutoff=0.8)
                hint = " (did you mean '%s'?)" % close[0] if close else ""
                return ("sidecar '%s': key '%s' is not allowed%s (allowed: %s)"
                        % (name, key, hint, ", ".join(_SIDECAR_KEYS)))
        env = sidecar.get("env")
        if env is not None and not isinstance(env, dict):
            return "sidecar '%s': env must be a mapping" % name
        for field, keys in (("env", list((env or {}).keys())),
                            ("envSecret", sidecar.get("envSecret") or []),
                            ("envPrivate", sidecar.get("envPrivate") or [])):
            if not isinstance(keys, list):
                return "sidecar '%s': %s must be a list" % (name, field)
            for key in keys:
                if not isinstance(key, str) or not _ENV_KEY.match(key):
                    return ("sidecar '%s': %s key '%s' is invalid: use "
                            "letters, digits, and underscores, not starting "
                            "with a digit" % (name, field, key))
        build = sidecar.get("build")
        if isinstance(build, dict):
            for key in build:
                if key not in _BUILD_KEYS:
                    return ("sidecar '%s': build option '%s' is not allowed "
                            "(allowed: %s)"
                            % (name, key, ", ".join(_BUILD_KEYS)))
            args = build.get("args")
            if args is not None and (
                    not isinstance(args, dict)
                    or not all(isinstance(k, str) and _scalar(v)
                               for k, v in args.items())):
                return ("sidecar '%s': build args must be a mapping of "
                        "names to single values" % name)
        elif build is not None and not isinstance(build, str):
            return ("sidecar '%s': build must be a context path or a "
                    "mapping" % name)
        resources = sidecar.get("resources")
        if resources is not None:
            if not isinstance(resources, dict):
                return "sidecar '%s': resources must be a mapping" % name
            for key in ("memory", "cpu"):
                if key in resources and not _quantity(resources[key]):
                    return ("sidecar '%s': resources %s '%s' is not a "
                            "quantity such as 512Mi or 0.5"
                            % (name, key, resources[key]))
    return None


# --- Web <-> sidecar env bridge -------------------------------------------- #

# Web pod env is a curated allowlist on both orchestrators, so an instance
# .env var a sidecar references (e.g. a service hostname) does NOT reach the
# web tier's getenv() on its own. This fragment, synced via
# config/settings/global, putenv()s those vars so MediaWiki Settings.php reads
# the same value the sidecar receives — "define once in .env, deliver to
# both". A sidecar can opt a key out via `envPrivate` (delivered to the
# sidecar, kept out of the web tier). Secret variables are always left out:
# the web tier gets those from `config set --secret --web`.
_BRIDGE_HEADER = (
    "<?php\n"
    "// Generated by canasta from config/sidecars.yaml — do not edit.\n"
    "// Bridges instance .env vars referenced by sidecars into the web tier's\n"
    "// getenv(), so Settings.php reads the same values the sidecars receive.\n"
    "// WARNING: values are written in cleartext; on k8s this file lives in the\n"
    "// gitops repo. Use a real secret store for highly sensitive values.\n"
)


def collect_env_refs(sidecars):
    """Instance .env var names referenced by any sidecar's env values or
    command, EXCEPT vars referenced only by env entries the sidecar lists in
    `envPrivate` (delivered to that sidecar but deliberately kept out of the
    web tier's getenv()). A var referenced by any non-private entry anywhere
    is still bridged. `command` refs are always public — secrets belong in env."""
    names = set()
    for sidecar in sidecars or []:
        private = set(sidecar.get("envPrivate") or [])
        values = [v for k, v in (sidecar.get("env") or {}).items()
                  if k not in private]
        command = sidecar.get("command")
        if command:
            values += command if isinstance(command, list) else [command]
        for value in values:
            if isinstance(value, str):
                for match in _ENV_REF.finditer(value):
                    names.add(match.group(1))
    return sorted(names)


def _php_single_quote(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


def render_env_bridge(sidecars, env, is_secret=lambda name: False):
    """PHP fragment that putenv()s the .env vars a sidecar references, or None
    when no referenced var is present in the instance .env (nothing to bridge).
    Secret variables are never bridged."""
    lines = []
    for name in collect_env_refs(sidecars):
        if name in env and not is_secret(name):
            lines.append("putenv('%s=%s');"
                         % (name, _php_single_quote(env[name])))
    if not lines:
        return None
    return _BRIDGE_HEADER + "\n".join(lines) + "\n"


def bridge_omitted_secrets(sidecars, env, is_secret):
    """Secret variables the bridge would otherwise have carried."""
    return [name for name in collect_env_refs(sidecars)
            if name in env and is_secret(name)]


def _refs(value):
    if not isinstance(value, str):
        return []
    return [match.group(1) for match in _ENV_REF.finditer(value)]


def read_env(instance_path, relpath=".env"):
    """Parse an instance env file (default .env) into a dict."""
    path = os.path.join(instance_path, relpath)
    env = {}
    if not os.path.exists(path):
        return env
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def read_names(instance_path, relpath):
    """Whitespace-separated names in an instance file (empty if absent)."""
    path = os.path.join(instance_path, relpath)
    if not os.path.exists(path):
        return set()
    with open(path) as handle:
        return set(handle.read().split())


def read_recorded_secret_names(instance_path):
    """Names set with `config set --secret`: config/secret-keys on Compose,
    the keys of config/secrets.env on Kubernetes."""
    names = set(read_env(instance_path, os.path.join("config", "secrets.env")))
    return names | read_names(instance_path,
                              os.path.join("config", "secret-keys"))


def secret_classifier(instance_path, secret_key_regex):
    """Predicate for secret variables: names matching the classifier regex
    or set with `config set --secret`."""
    secret_re = re.compile(secret_key_regex)
    recorded = read_recorded_secret_names(instance_path)

    def is_secret(name):
        return name in recorded or bool(secret_re.match(name))
    return is_secret


def validate_k8s_secret_refs(sidecars, is_secret):
    """Return None unless a sidecar would get a secret variable as a literal
    in the rendered Kubernetes values, else an error. Only env keys listed in
    envSecret are taken from the app Secret instead of being resolved."""
    for sidecar in sidecars or []:
        name = sidecar.get("name", "?")
        secret_keys = set(sidecar.get("envSecret") or [])
        for key, value in (sidecar.get("env") or {}).items():
            if key in secret_keys:
                continue
            for var in _refs(value):
                if is_secret(var):
                    return ("sidecar '%s': env %s references the secret "
                            "variable %s, which Kubernetes would store in "
                            "cleartext; set the value with 'canasta config set "
                            "--secret %s=VALUE' and list %s in the sidecar's "
                            "envSecret" % (name, key, var, key, key))
        command = sidecar.get("command")
        if not isinstance(command, list):
            command = [command]
        for part in command:
            for var in _refs(part):
                if is_secret(var):
                    return ("sidecar '%s': command references the secret "
                            "variable %s, which Kubernetes would store in "
                            "cleartext; pass it in env instead, set with "
                            "'canasta config set --secret KEY=VALUE' and "
                            "listed in the sidecar's envSecret" % (name, var))
    return None


# --- Docker Compose -------------------------------------------------------- #

_MEM_UNITS = {
    "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4,
    "k": 1000, "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4,
}


def k8s_memory_to_bytes(value):
    """Convert a Kubernetes memory quantity ('256Mi', '1Gi', '512M') to an
    integer byte count, which Docker Compose accepts verbatim — Compose rejects
    the k8s 'Mi'/'Gi' suffixes. A bare number is bytes; an unrecognized value
    is returned unchanged. (The k8s render keeps the original notation.)"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    # Two-char binary suffixes (Mi) before single-char decimal (M).
    for suffix in ("Ki", "Mi", "Gi", "Ti", "K", "M", "G", "T", "k"):
        if text.endswith(suffix):
            try:
                return int(float(text[:-len(suffix)]) * _MEM_UNITS[suffix])
            except ValueError:
                return value
    return value


def _compose_healthcheck(healthcheck):
    if healthcheck.get("command"):
        test = ["CMD"] + list(healthcheck["command"])
    elif healthcheck.get("tcp"):
        test = ["CMD-SHELL", "nc -z localhost %s || exit 1" % healthcheck["tcp"]]
    elif healthcheck.get("path"):
        port = healthcheck.get("port", 80)
        test = ["CMD", "curl", "-f",
                "http://localhost:%s%s" % (port, healthcheck["path"])]
    else:  # bare port -> TCP
        test = ["CMD-SHELL",
                "nc -z localhost %s || exit 1" % healthcheck.get("port")]
    return {"test": test, "interval": "30s", "timeout": "5s", "retries": 3}


def render_compose_service(sidecar):
    """One sidecar -> (compose service dict, {named_volume: None})."""
    service = {}
    if sidecar.get("build"):
        service["build"] = sidecar["build"]
    else:
        service["image"] = sidecar["image"]
    if sidecar.get("command"):
        # Compose accepts a string or list and interpolates ${VAR} itself.
        service["command"] = sidecar["command"]
    service["restart"] = "unless-stopped"
    if sidecar.get("env"):
        service["environment"] = dict(sidecar["env"])
    if sidecar.get("ports"):
        service["expose"] = [str(p) for p in sidecar["ports"]]
    named_volumes = {}
    mounts = []
    for volume in sidecar.get("volumes", []) or []:
        if volume.get("persistent"):
            vol = "%s-%s" % (sidecar["name"], volume["name"])
            mounts.append("%s:%s" % (vol, volume["mountPath"]))
            named_volumes[vol] = None
        else:
            mounts.append(volume["mountPath"])  # anonymous ephemeral
    for fileref in sidecar.get("files", []) or []:
        suffix = ":ro" if fileref.get("readOnly", True) else ""
        mounts.append("./%s:%s%s"
                      % (fileref["source"], fileref["mountPath"], suffix))
    if mounts:
        service["volumes"] = mounts
    if sidecar.get("depends_on"):
        service["depends_on"] = list(sidecar["depends_on"])
    if sidecar.get("healthcheck"):
        service["healthcheck"] = _compose_healthcheck(sidecar["healthcheck"])
    if sidecar.get("resources"):
        limits = {}
        if sidecar["resources"].get("memory"):
            # Compose wants memory as a string byte count (it rejects both the
            # k8s 'Mi'/'Gi' suffix and a bare integer).
            limits["memory"] = str(k8s_memory_to_bytes(
                sidecar["resources"]["memory"]))
        if sidecar["resources"].get("cpu"):
            limits["cpus"] = str(sidecar["resources"]["cpu"])
        if limits:
            service["deploy"] = {"resources": {"limits": limits}}
    return service, named_volumes


def render_compose(sidecars):
    """Sidecars -> a docker-compose override dict, or None when there are none."""
    if not sidecars:
        return None
    services = {}
    volumes = {}
    for sidecar in sidecars:
        service, named = render_compose_service(sidecar)
        services[sidecar["name"]] = service
        volumes.update(named)
    override = {"services": services}
    if volumes:
        override["volumes"] = volumes
    return override


# --- Kubernetes ------------------------------------------------------------ #

def render_k8s_values(sidecars, env, file_reader):
    """Sidecars -> the list for `.Values.sidecars` (env resolved, file
    contents inlined for ConfigMaps). `file_reader(source)` returns the text
    of an instance file. Returns [] when there are no sidecars."""
    out = []
    for sidecar in sidecars or []:
        item = {"name": sidecar["name"]}
        if sidecar.get("build"):
            item["build"] = sidecar["build"]
        else:
            item["image"] = sidecar["image"]
        if sidecar.get("command"):
            command = sidecar["command"]
            if isinstance(command, list):
                # Resolve each element — k8s never interpolates ${VAR}.
                item["command"] = [resolve_env_value(c, env) for c in command]
            else:
                item["command"] = resolve_env_value(command, env)
        # envSecret keys are sourced from the per-instance app Secret (rendered
        # as secretKeyRef by the chart), so they are NOT resolved to cleartext
        # here. Everything else stays a resolved literal, exactly as before.
        secret_keys = set(sidecar.get("envSecret") or [])
        if sidecar.get("env"):
            plain = {k: resolve_env_value(v, env)
                     for k, v in sidecar["env"].items() if k not in secret_keys}
            if plain:
                item["env"] = plain
        if secret_keys:
            item["envSecret"] = sorted(secret_keys)
        if sidecar.get("ports"):
            item["ports"] = [int(p) for p in sidecar["ports"]]
        if sidecar.get("volumes"):
            item["volumes"] = sidecar["volumes"]
        if sidecar.get("files"):
            item["files"] = [
                {"mountPath": fileref["mountPath"],
                 "readOnly": bool(fileref.get("readOnly", True)),
                 "content": file_reader(fileref["source"])}
                for fileref in sidecar["files"]
            ]
        if sidecar.get("depends_on"):
            item["depends_on"] = list(sidecar["depends_on"])
        if sidecar.get("healthcheck"):
            item["healthcheck"] = sidecar["healthcheck"]
        if sidecar.get("resources"):
            item["resources"] = sidecar["resources"]
        out.append(item)
    return out
