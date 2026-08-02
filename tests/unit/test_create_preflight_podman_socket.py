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
        task = _named("Check container runtime is available")
        assert "podman" in task["when"]
        inner = task["block"][0]
        assert inner["ansible.builtin.command"]["cmd"] == "podman info"


class TestTheProbesDoNotClobberEachOther:
    """Two conditional probes sharing one register name is a live failure.

    Exactly one of the two probe blocks runs; the other is skipped. A
    skipped task still registers, and the result it stores has no `rc`
    and no `stdout`. So when both blocks registered `_docker_check`, the
    skipped one overwrote the successful one, `rc` fell back to the
    `default(1)`, and `create` aborted with "No container runtime is
    available" on a host where `podman info` had just succeeded.

    The same clobbering emptied the `docker info` output that the disk
    check parses its storage root from, silently skipping it.
    """

    def _registers(self):
        """Every register name in the preflight, with its task name."""
        found = []

        def walk(tasks):
            for t in tasks or []:
                if "register" in t:
                    found.append((t["register"], t.get("name")))
                for key in ("block", "rescue", "always"):
                    walk(t.get(key))

        walk(_block_tasks())
        return found

    def test_no_register_name_is_used_twice(self):
        names = [r for r, _ in self._registers()]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, (
            "these registers are set by more than one task, so whichever "
            "task is skipped will overwrite the other's result: %s"
            % sorted(dupes)
        )

    def test_each_probe_registers_distinctly(self):
        by_task = dict((n, r) for r, n in self._registers())
        assert by_task["Probe podman info (compose_command is podman-based)"] \
            != by_task["Probe docker info"]
        assert by_task["Probe docker info"] \
            != by_task["Probe podman info (fallback)"]

    def test_the_failure_check_reads_the_collapsed_result(self):
        # Reading any single probe register here is what broke: the one
        # that answered may not be the one this task can see.
        task = _named("Fail if container runtime not available")
        assert "_runtime_check" in str(task["when"])
        for probe in ("_podman_only_probe", "_docker_probe",
                      "_podman_fallback_probe"):
            assert probe not in str(task["when"])

    def test_the_collapsed_result_keeps_only_a_probe_that_succeeded(self):
        expr = _named("Record the runtime probe that answered")[
            "ansible.builtin.set_fact"]["_runtime_check"]
        assert "selectattr('rc', 'defined')" in expr, (
            "a skipped probe has no rc and must be filtered out"
        )
        assert "selectattr('rc', 'equalto', 0)" in expr

    def test_docker_outranks_the_podman_fallback(self):
        # On a host running both, the recorded storage root must describe
        # the runtime create will actually use.
        expr = _named("Record the runtime probe that answered")[
            "ansible.builtin.set_fact"]["_runtime_check"]
        assert expr.index("_docker_probe") < expr.index(
            "_podman_fallback_probe")

    def test_the_storage_root_reads_the_collapsed_result(self):
        expr = _named("Resolve container storage root")[
            "ansible.builtin.set_fact"]["_docker_root_dir"]
        assert "_runtime_check.stdout" in expr
