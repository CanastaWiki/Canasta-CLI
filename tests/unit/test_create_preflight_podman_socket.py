"""A Podman socket means Podman, whatever `docker info` says.

On a host that has the Docker CLI installed alongside rootless Podman,
the socket probe points DOCKER_HOST at
`unix:///run/user/<uid>/podman/podman.sock`. `docker info` then succeeds
— the Docker CLI is talking to Podman's Docker-compatible API — so the
Docker-first probe concluded Docker and recorded:

    "dockerHost":      "unix:///run/user/1000/podman/podman.sock",
    "composeCommand":  "docker compose",
    "inspectCommand":  "docker"

The containers run under Podman regardless, so create succeeds and the
mis-recording only surfaces on a later upgrade, which then runs
`docker compose` against a host whose Docker daemon is not running.

The socket answers the question on its own, so the decision is made from
it before any probe runs.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PREFLIGHT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml")


def _block_tasks():
    with open(PREFLIGHT) as f:
        doc = yaml.safe_load(f)
    return doc[0]["block"]


def _index_of(name):
    for i, t in enumerate(_block_tasks()):
        if t.get("name") == name:
            return i
    raise AssertionError("task not found: %s" % name)


def _named(name):
    return _block_tasks()[_index_of(name)]


class TestTheSocketDecidesTheRuntime:
    def test_podman_socket_selects_podman(self):
        task = _named("Use Podman when the socket in use is Podman's")
        facts = task["ansible.builtin.set_fact"]
        assert facts["compose_command"] == "podman-compose"
        assert facts["inspect_command"] == "podman"

    def test_it_keys_on_the_resolved_socket(self):
        task = _named("Use Podman when the socket in use is Podman's")
        conditions = " ".join(task["when"])
        assert "_preflight_docker_host" in conditions
        assert "podman" in conditions

    def test_it_does_not_override_an_explicit_podman_command(self):
        # compose_command already naming podman (registry or .env) is
        # authoritative; re-setting it would be harmless but the guard
        # keeps the two sources from fighting.
        task = _named("Use Podman when the socket in use is Podman's")
        conditions = " ".join(task["when"])
        assert "compose_command" in conditions

    def test_the_resolved_socket_mirrors_the_playbook_precedence(self):
        task = _named("Resolve the socket the runtime probes will use")
        expr = task["ansible.builtin.set_fact"]["_preflight_docker_host"]
        # Same order as the DOCKER_HOST expression in canasta.yml, or the
        # preflight would decide from a different socket than the probes use.
        assert expr.index("docker_host") < expr.index("instance_docker_host")
        assert (expr.index("instance_docker_host")
                < expr.index("_detected_docker_host"))


class TestItRunsBeforeTheProbes:
    def test_decision_precedes_the_docker_probe(self):
        decision = _index_of("Use Podman when the socket in use is Podman's")
        probe = _index_of("Probe Docker then Podman for remote/detected runtime")
        assert decision < probe, (
            "the socket decision must run before the Docker-first probe, "
            "or the probe still wins on a Podman host with the Docker CLI"
        )

    def test_decision_follows_socket_detection(self):
        detect = _index_of("Detect rootless container socket on target")
        decision = _index_of("Use Podman when the socket in use is Podman's")
        assert detect < decision, (
            "_detected_docker_host is not set until the socket probe runs"
        )

    def test_the_podman_branch_still_probes_the_runtime(self):
        # Selecting podman early must land in the branch that registers
        # _docker_check, or 'Fail if container runtime not available'
        # fires on a perfectly good Podman host.
        task = _named("Check container runtime is available")
        assert "podman" in task["when"]
        inner = task["block"][0]
        assert inner["ansible.builtin.command"]["cmd"] == "podman info"
        assert inner["register"] == "_docker_check"
