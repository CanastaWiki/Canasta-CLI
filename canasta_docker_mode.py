"""Preflight checks for the dockerized CLI (canasta-docker), which runs
inside the canasta-ansible container and so cannot do everything a native
install can: drive Podman, install onto its own host, or read a key file
outside the directories the wrapper mounts."""

import os
import sys

import canasta_config
from direct_commands._helpers import _is_local_target, _resolve_compose_cmd


def _runs_under_podman(instance):
    """True when this instance's compose calls need Podman tooling.

    Asks the same resolver the compose calls go through rather than
    testing the registry fields directly. Those fields are a precedence
    chain — an explicit composeCommand wins over .env, which wins over
    inspectCommand, which wins over the dockerHost socket — so reading
    them as a set disagrees with reality at both ends: an instance
    recorded as `docker compose` against a Podman socket really does run
    `docker compose` (which the container has), and one whose only
    statement is `compose_command` in .env really does need
    podman-compose.
    """
    return "podman" in " ".join(_resolve_compose_cmd(instance))


def check_docker_mode_can_reach_runtime(args):
    """Refuse a Podman instance that is local to a Docker-mode controller.

    In Docker mode the CLI runs inside the canasta-ansible container,
    which ships docker and `docker compose` but no podman-compose. For a
    *remote* instance that does not matter — compose runs on the target
    over SSH, where podman-compose lives. For an instance local to the
    controller the compose call runs in the container and dies partway
    through with a bare `rc=127`, after stack files have already been
    rewritten.

    Installing podman-compose on the host does not help: the command
    runs inside the container, and only the socket, the registry, $HOME
    and the working directory are mounted — not host binaries. So the
    honest answer is to refuse up front and name the native CLI.
    """
    if os.environ.get("CANASTA_RUN_MODE") != "docker":
        return
    conf_file = canasta_config.config_path(canasta_config.get_config_dir())
    if not os.path.isfile(conf_file):
        return
    instances = canasta_config.read_config(
        canasta_config.get_config_dir()).get("Instances", {})

    # Scope to the instance the command names; commands that take no
    # target (upgrade) act on every registered instance, so check them
    # all rather than letting one bad record fail the run halfway.
    inst_id = getattr(args, "id", None)
    if inst_id:
        candidates = {inst_id: instances[inst_id]} if inst_id in instances else {}
    else:
        inst_path = getattr(args, "path", None)
        if inst_path:
            target = os.path.abspath(inst_path)
            candidates = {
                k: v for k, v in instances.items()
                if os.path.abspath(v.get("path", "")) == target
            }
        else:
            candidates = instances

    blocked = sorted(
        k for k, v in candidates.items()
        if v.get("host") in ("localhost", "", None)
        and _runs_under_podman(v)
    )
    if not blocked:
        return

    print(
        "Error: %s runs under Podman on this controller, which the "
        "Docker-mode CLI cannot manage.\n"
        "The CLI runs inside a container that has no podman-compose, and "
        "installing one on the host does not reach it.\n"
        "Install the native CLI to manage %s:\n"
        "  curl -fsSL https://get.canasta.wiki | bash -s -- --native\n"
        "Instances on remote hosts are unaffected — their compose "
        "commands run on the target."
        % (
            "instance '%s'" % blocked[0] if len(blocked) == 1
            else "instances %s" % ", ".join("'%s'" % b for b in blocked),
            "it" if len(blocked) == 1 else "them",
        ),
        file=sys.stderr,
    )
    sys.exit(1)


def check_docker_mode_install_target(args):
    """Refuse a *local* `canasta install` under the Docker-mode CLI.

    `canasta install` provisions host-level software (the docker engine
    package, podman, etc.), which the canasta-ansible
    container cannot do: it has no systemd and no privilege over the
    host package manager. A *remote* install is fine — the playbook runs
    over SSH on the target named by -H/--host, or resolved from -i/--id
    through the registry, where systemd and root exist.

    The decision lives here rather than in the canasta-docker wrapper
    because only argparse has already parsed the target: it handles the
    attached forms the wrapper had to scan for (-Hprod1.example.com,
    -imysite), and flags that appear before the command (e.g.
    'canasta -v install docker') never put 'install' in $1. The registry
    lookup also sees the shape `canasta create` actually writes: an
    instance registered without --host has no 'host' key at all, which
    means it is local.

    Called from main() for the `install` command only; every other
    command targets an existing instance's runtime and is covered by
    check_docker_mode_can_reach_runtime instead.
    """
    if os.environ.get("CANASTA_RUN_MODE") != "docker":
        return

    host = getattr(args, "host", None)
    inst_id = getattr(args, "id", None)
    if host:
        # -H/--host names the controller itself: local, refuse. Any other
        # host is remote and passes through. --host wins over --id if both
        # are given, so a -H here settles it without reading the registry.
        if _is_local_target(host):
            _refuse_local_docker_install()
        return

    if inst_id:
        instances = canasta_config.read_config(
            canasta_config.get_config_dir()).get("Instances", {})
        inst = instances.get(inst_id)
        if inst is None:
            print(
                "Error: instance '%s' is not registered, so there is no "
                "host to install on. Register it with 'canasta create' or "
                "'canasta host add'." % inst_id,
                file=sys.stderr,
            )
            sys.exit(1)
        if not _is_local_target(inst.get("host") or "localhost"):
            return

    _refuse_local_docker_install()


def check_docker_mode_key_path(args):
    """Refuse a gitops init --key that canasta-docker would lose.

    The canasta-docker container mounts the host's $HOME, the config
    directory and the working directory, each at the same path. A key
    exported anywhere else is written inside the container and discarded
    when the command exits, after init has already pushed the repository,
    so refuse it before init starts.
    """
    if os.environ.get("CANASTA_RUN_MODE") != "docker":
        return
    key = getattr(args, "key", None)
    if not key:
        return
    path = os.path.realpath(os.path.expanduser(key))
    roots = [os.environ.get("HOME"), canasta_config.get_config_dir(), os.getcwd()]
    for root in filter(None, roots):
        root = os.path.realpath(root)
        if path == root or path.startswith(root + os.sep):
            return
    print(
        "Error: --key %s is outside the directories canasta-docker shares "
        "with this machine (your home directory, %s, and the current "
        "directory), so the key would be lost when the command exits. Use a "
        "path under your home directory, for example "
        "--key ~/canasta-gitops.key." % (key, canasta_config.get_config_dir()),
        file=sys.stderr,
    )
    sys.exit(1)


def _refuse_local_docker_install():
    """Explain why a local install cannot run in Docker mode, then exit 1."""
    print(
        "Error: 'canasta install' is not supported in canasta-docker mode.\n"
        "\n"
        "These commands install system packages on the host (docker "
        "engine, podman, etc.) which can't run from inside a container.\n"
        "\n"
        "Either:\n"
        "  - Use canasta-native:\n"
        "      curl -fsSL https://get.canasta.wiki | bash -s -- --native\n"
        "  - Or install the dependency manually on the host and use\n"
        "    canasta-docker for the rest of the workflow (create, start,\n"
        "    backup, restore, etc.).\n"
        "\n"
        "Or install on a remote host with:\n"
        "      canasta install <package> -i <instance-id>\n"
        "which runs the install over SSH on the host where the instance "
        "lives.",
        file=sys.stderr,
    )
    sys.exit(1)
