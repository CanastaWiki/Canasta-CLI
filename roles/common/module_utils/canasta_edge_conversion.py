# -*- coding: utf-8 -*-
"""Reading an instance's files for the canasta_edge module, and planning
which of a host's instances move behind the edge."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os

import yaml

try:
    from ansible.module_utils.canasta_atomic_write import read_text as _read
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_atomic_write import read_text as _read
try:
    from ansible.module_utils.canasta_caddyfile import (
        instance_tls_directives, tls_directives_problem,
    )
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_caddyfile import instance_tls_directives, tls_directives_problem
try:
    from ansible.module_utils.canasta_edge_render import (
        EdgeError, check_together, compose_predates_edge, instance_from_config, rates_set,
    )
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_edge_render import (
        EdgeError, check_together, compose_predates_edge, instance_from_config, rates_set,
    )
try:
    from ansible.module_utils.canasta_edge_writable import LAYER_FILE
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_edge_writable import LAYER_FILE
try:
    from ansible.module_utils.canasta_env_keys import modernize_values
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_env_keys import modernize_values
try:
    from ansible.module_utils.canasta_env_lines import entries_to_dict, parse_env_file
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_env_lines import entries_to_dict, parse_env_file


def read_env(instance_path):
    """The instance's .env as a dict, unquoted as canasta_env reads it."""
    content = _read(os.path.join(instance_path, ".env")) or ""
    return modernize_values(entries_to_dict(parse_env_file(content)))


def tls_directives(path):
    """'file:line' for each tls directive in the instance's Caddyfile.site
    and Caddyfile.global."""
    return instance_tls_directives(lambda rel: _read(os.path.join(path, rel)))


def _edge_view(instance_id, path, env):
    """The renderer's view of an instance as it would be behind the edge;
    raises EdgeError when the edge cannot serve it."""
    wikis = (yaml.safe_load(_read(os.path.join(path, "config", "wikis.yaml")) or "")
             or {}).get("wikis") or []
    media = (yaml.safe_load(_read(os.path.join(path, "config", "media-hosts.yaml")) or "")
             or {}).get("media_hosts") or []
    return instance_from_config(instance_id, env, wikis,
                                [m for m in media if (m.get("hostname") or "").strip()])


def _ineligible(instance_id, path, env):
    """Why an instance cannot move behind the edge, or None if it can."""
    try:
        _edge_view(instance_id, path, env)
    except EdgeError as exc:
        return str(exc)
    return None


def _writable(path):
    env_file = os.path.join(path, ".env")
    return os.access(path, os.W_OK) and (
        not os.path.exists(env_file) or os.access(env_file, os.W_OK))


def daemon_rootless(run, inspect, docker_host=""):
    """Whether the Docker daemon at docker_host runs rootless. Its port
    forwarding then replaces client addresses, as rootless Podman's does."""
    env = {"DOCKER_HOST": docker_host} if docker_host else None
    rc, out, _ = run([inspect, "info", "--format", "{{json .SecurityOptions}}"], env=env)
    return rc == 0 and "name=rootless" in out


def predates_edge(path):
    """Whether the instance's docker-compose.yml predates the edge."""
    return compose_predates_edge(_read(os.path.join(path, "docker-compose.yml")))


def behind_edge(path):
    """Whether an instance is behind the edge already: its edge layer
    exists, which only the edge sync writes."""
    return os.path.exists(os.path.join(path, LAYER_FILE))


def plan_conversion(instances, rootless=lambda inst: False, podman_ready=lambda: True):
    """Decide how a host's Compose instances move behind the edge, which is
    every Compose instance's front door.

    instances: [{id, path, compose_command}]. Every instance not yet behind the edge moves.
    If any of them cannot, the host is refused as a whole and none of its
    instances is upgraded, since none could come back without a front door.
    Returns {convert: [ids], rootless: [ids moving on rootless Docker],
    rated: [those of them that set the per-client rates], refuse: reason or
    None, held: [Compose instance ids a refusal holds back]}."""
    compose = [i for i in instances if os.path.isdir(i["path"])]
    names = ", ".join("'%s'" % i["id"] for i in compose)

    def refuse(reason):
        return {"convert": [], "rootless": [], "rated": [], "refuse": reason,
                "held": [i["id"] for i in compose]}

    families = {"podman" if "podman" in (i.get("compose_command") or "") else "docker"
                for i in compose}
    if len(families) > 1:
        return refuse("its instances use both Docker and Podman, which cannot share one "
                      "HAProxy edge (%s). Move one runtime's instances to another host, or "
                      "onto the same runtime, and upgrade again." % names)
    if families == {"docker"} and len({bool(rootless(i)) for i in compose}) > 1:
        return refuse("its instances use both rootful and rootless Docker, which cannot "
                      "share one HAProxy edge (%s). Move one daemon's instances to another "
                      "host, or onto the same daemon, and upgrade again." % names)
    convert, on_rootless, rated, problems = [], [], [], []
    for inst in compose:
        if behind_edge(inst["path"]):
            continue
        env = read_env(inst["path"])
        podman = "podman" in (inst.get("compose_command") or "")
        if not _writable(inst["path"]):
            problems.append("'%s': the CLI cannot write to its directory" % inst["id"])
        elif podman and not podman_ready():
            problems.append("'%s': under rootless Podman the edge needs the operator's systemd "
                            "user session, which is not running; enable lingering for this "
                            "user ('canasta install podman' does)" % inst["id"])
        else:
            reason = _ineligible(inst["id"], inst["path"], env)
            if reason:
                problems.append(reason)
                continue
            found = tls_directives(inst["path"])
            if found:
                problems.append("'%s' %s" % (inst["id"], tls_directives_problem(found)))
                continue
            convert.append(inst["id"])
            # Rootless Podman reports rootless too, but its edge takes its
            # sockets from systemd and keeps client addresses.
            if not podman and rootless(inst):
                on_rootless.append(inst["id"])
                if rates_set(env):
                    rated.append(inst["id"])
    if problems:
        return refuse("the HAProxy edge cannot serve every instance on it yet: %s. Fix "
                      "that and upgrade again." % "; ".join(problems))
    # Each could move on its own; together they must not clash on a port.
    if convert:
        try:
            check_together([_edge_view(i["id"], i["path"], read_env(i["path"]))
                            for i in compose])
        except EdgeError as exc:
            return refuse(str(exc))
    return {"convert": convert, "rootless": on_rootless, "rated": rated, "refuse": None,
            "held": []}
