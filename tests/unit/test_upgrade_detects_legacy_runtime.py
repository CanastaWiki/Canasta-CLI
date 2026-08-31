"""An instance with no recorded runtime must be asked, not assumed.

composeCommand/inspectCommand became registry fields after instances
were already registered, so every instance created before them reads
back as Docker. _upgrade_single.yml takes the registry at its word, and
on a Podman-only host the whole upgrade runs `docker compose`:

    Pulling Canasta container images...
    Error: pull Compose images failed (rc=127): /bin/sh: 1: docker: not found

with `canasta doctor` on the same host reporting Docker MISSING and
Podman OK. The probe has to run on the instance's host (the controller's
PATH says nothing about it) and the answer has to reach the registry, or
`canasta list` and `canasta start` keep resolving that instance to
Docker afterwards.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SINGLE = os.path.join(REPO_ROOT, "playbooks", "_upgrade_single.yml")
DETECT = os.path.join(
    REPO_ROOT, "roles", "common", "tasks", "detect_runtime.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _tasks(path):
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    walk(_load(path))
    return out


def _named(path, needle):
    return next(
        (t for t in _tasks(path)
         if needle.lower() in str(t.get("name", "")).lower()), None)


CONFIRM = "Confirm the recorded runtime against the instance's host"


class TestUpgradeChecksTheRecordedRuntime:
    def test_the_detection_is_included(self):
        task = _named(SINGLE, "Confirm the recorded runtime")
        assert task, (
            "_upgrade_single.yml takes the registry at its word, so an "
            "instance that records nothing — or records `docker compose` "
            "from a create that misread a Podman socket — runs the whole "
            "upgrade with `docker compose`"
        )
        assert task["ansible.builtin.include_tasks"].endswith(
            "roles/common/tasks/detect_runtime.yml")

    def test_it_runs_for_every_compose_instance(self):
        # Gating on "nothing recorded" only caught pre-field instances.
        # A record holding the wrong runtime is the case that actually
        # fails today, and it never re-probed.
        cond = _named(SINGLE, "Confirm the recorded runtime")["when"]
        conds = cond if isinstance(cond, list) else [cond]
        assert not any("composeCommand" in str(c) for c in conds), (
            "re-probing only when composeCommand is absent leaves an "
            "instance that recorded the wrong runtime unrepairable"
        )
        assert any("compose" in str(c) and "orchestrator" in str(c)
                   for c in conds), (
            "Kubernetes instances have no compose runtime to probe"
        )

    def test_it_runs_after_the_connection_switch(self):
        # The controller's PATH is not evidence about the instance's host.
        names = [str(t.get("name", "")) for t in _tasks(SINGLE)]
        assert (names.index("Switch connection to instance host")
                < names.index(CONFIRM))

    def test_the_facts_are_reset_before_the_probe(self):
        # set_fact persists across loop iterations, so a podman instance
        # detected in one pass must not leak into the next instance.
        names = [str(t.get("name", "")) for t in _tasks(SINGLE)]
        assert (names.index("Set instance facts") < names.index(CONFIRM))

    def test_the_socket_is_bound_per_instance(self):
        # canasta.yml's play-level DOCKER_HOST reads instance_docker_host;
        # left unset in the loop it keeps the previous instance's socket.
        facts = _named(SINGLE, "Set instance facts")["ansible.builtin.set_fact"]
        assert "instance_docker_host" in facts
        assert "dockerHost" in facts["instance_docker_host"]


class TestTheProbeAsksTheHost:
    def test_it_probes_both_runtimes(self):
        cmds = [t["ansible.builtin.command"]["cmd"] for t in _tasks(DETECT)
                if "ansible.builtin.command" in t]
        assert "docker info" in cmds
        assert "podman info" in cmds

    def test_a_missing_binary_does_not_abort_the_upgrade(self):
        # `docker info` where docker is absent leaves no rc at all.
        for t in _tasks(DETECT):
            if "ansible.builtin.command" not in t:
                continue
            assert t["failed_when"] is False
            assert t["changed_when"] is False
        expr = str(_named(DETECT, "Decide the runtime for this instance")
                   ["ansible.builtin.set_fact"]["_dr_use_podman"])
        assert expr.count("default(1)") == 2, (
            "the conditions read .rc directly, which is undefined when the "
            "binary is missing — the case this exists to handle"
        )

    def test_podman_wins_only_when_docker_is_unavailable(self):
        expr = str(_named(DETECT, "Decide the runtime for this instance")
                   ["ansible.builtin.set_fact"]["_dr_use_podman"])
        assert "(_dr_docker.rc | default(1)) != 0" in expr
        assert "(_dr_podman.rc | default(1)) == 0" in expr
        assert _named(DETECT, "Use Podman when it is this instance's runtime")

    def test_a_declared_runtime_overrides_both_probes(self):
        # A host carrying both runtimes cannot express which one its
        # instances belong to through probing alone.
        for name in ("Probe docker info", "Probe podman info"):
            conds = [str(c) for c in _named(DETECT, name)["when"]]
            assert any("container_runtime" in c for c in conds), (
                "%s must not run once the runtime has been declared" % name
            )
        expr = str(_named(DETECT, "Decide the runtime for this instance")
                   ["ansible.builtin.set_fact"]["_dr_use_podman"])
        assert "container_runtime == 'podman'" in expr

    def test_the_result_is_written_back_to_the_registry(self):
        task = _named(DETECT, "Record the detected runtime")
        assert task, (
            "without the write-back the probe repeats every upgrade, and "
            "list/start/exec keep resolving the instance to Docker"
        )
        assert task["canasta_registry"]["state"] == "update"
        assert task["canasta_registry"]["compose_command"] == "podman-compose"
        assert task["canasta_registry"]["inspect_command"] == "podman"

    def test_the_registry_write_runs_on_the_controller(self):
        task = _named(DETECT, "Record the detected runtime")
        assert task["delegate_to"] == "canasta_controller"
        assert task["vars"]["ansible_connection"] == "local"


class TestAPodmanSocketIsNotAskedAboutDocker:
    def test_the_socket_is_noted_before_probing(self):
        names = [str(t.get("name", "")) for t in _tasks(DETECT)]
        assert (names.index("Note whether this instance's socket is Podman's")
                < names.index("Probe docker info"))

    def test_docker_is_not_probed_on_a_podman_socket(self):
        # `docker info` succeeds against Podman's Docker-compatible API,
        # so asking it would confirm a wrong `docker compose` record
        # rather than correct it.
        task = _named(DETECT, "Probe docker info")
        assert "_dr_podman_socket" in str(task["when"])

    def test_the_podman_probe_still_runs_when_docker_was_skipped(self):
        # A skipped docker probe leaves _dr_docker undefined; the podman
        # probe must treat that as "Docker did not answer", or a Podman
        # socket ends up with no runtime detected at all.
        task = _named(DETECT, "Probe podman info")
        assert "default(1)" in str(task["when"])
