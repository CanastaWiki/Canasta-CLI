"""The socket probe must prefer Docker, and defer to a declared runtime.

The probe looked for a rootless Podman socket before Docker's. A host with
Docker installed, running and healthy, plus a rootless `podman.socket` enabled
for unrelated reasons, was recorded as `composeCommand: podman-compose` — an
unsupported runtime the operator never chose, visible afterwards only as an
empty `docker ps`.
"""
import os
import re
import subprocess

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DETECT_SOCKET = os.path.join(
    REPO_ROOT, "roles", "common", "tasks", "detect_docker_socket.yml",
)
PREFLIGHT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml",
)


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _named(path, name):
    with open(path) as fh:
        for task in _walk(yaml.safe_load(fh)):
            if (task.get("name") or "") == name:
                return task
    return None


def _probe_script():
    return _named(DETECT_SOCKET, "Probe target for a container runtime")[
        "ansible.builtin.shell"]["cmd"]


class TestProbeOrder:
    def test_docker_is_tried_before_podman(self):
        script = _probe_script()
        assert re.search(r"try_docker \|\| try_podman", script), (
            "podman must only be reached when docker did not answer"
        )

    def test_the_system_docker_socket_must_answer_not_merely_exist(self):
        # An operator outside the docker group sees the socket and cannot
        # use it; that host really does belong on its rootless runtime.
        script = _probe_script()
        assert "docker -H unix:///var/run/docker.sock info" in script

    def test_nothing_found_still_falls_back_to_the_default_socket(self):
        assert "|| printf 'docker -\\n'" in _probe_script()


class TestProbeScriptBehavior:
    """Run the probe's own shell against a synthetic filesystem."""

    def _run(self, tmp_path, want="", docker_answers=False,
             rootless_docker=False, podman=False, system_docker=False):
        xdg = tmp_path / "run"
        xdg.mkdir()
        script = _probe_script().replace(
            "{{ container_runtime | default('') }}", want,
        )
        # Rewrite the two absolute system-docker probes onto the sandbox.
        sysdock = tmp_path / "docker.sock"
        script = script.replace("/var/run/docker.sock", str(sysdock))
        if system_docker:
            (tmp_path / "docker.sock").write_bytes(b"")
        if rootless_docker:
            (xdg / "docker.sock").write_bytes(b"")
        if podman:
            (xdg / "podman").mkdir()
            (xdg / "podman" / "podman.sock").write_bytes(b"")
        # A socket file rather than a real socket: swap the -S tests for -e
        # so the script's own branching is what is under test.
        script = script.replace("[ -S ", "[ -e ")
        stub = tmp_path / "bin"
        stub.mkdir()
        (stub / "docker").write_text(
            "#!/bin/sh\nexit %d\n" % (0 if docker_answers else 1))
        (stub / "docker").chmod(0o755)
        out = subprocess.run(
            ["sh", "-c", script],
            capture_output=True, text=True,
            env={"XDG_RUNTIME_DIR": str(xdg),
                 "PATH": "%s:/usr/bin:/bin" % stub},
        )
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def test_docker_wins_over_a_podman_socket_on_the_same_host(self, tmp_path):
        assert self._run(tmp_path, system_docker=True, docker_answers=True,
                         podman=True) == "docker -"

    def test_podman_is_used_when_docker_does_not_answer(self, tmp_path):
        assert self._run(tmp_path, system_docker=True, docker_answers=False,
                         podman=True).startswith("podman unix://")

    def test_rootless_docker_is_preferred_over_podman(self, tmp_path):
        assert self._run(tmp_path, rootless_docker=True, podman=True
                         ).startswith("docker unix://")

    def test_a_declared_runtime_probes_only_that_runtime(self, tmp_path):
        # Podman present and Docker healthy, but podman was asked for.
        assert self._run(tmp_path, want="podman", system_docker=True,
                         docker_answers=True, podman=True
                         ).startswith("podman unix://")

    def test_a_declared_runtime_that_is_absent_reports_none(self, tmp_path):
        # Rather than quietly using the other one.
        assert self._run(tmp_path, want="podman", system_docker=True,
                         docker_answers=True) == "none -"

    def test_bare_host_falls_back_to_the_default_docker_socket(self, tmp_path):
        assert self._run(tmp_path) == "docker -"


class TestDeclaredRuntime:
    def test_an_unrecognized_value_is_rejected(self):
        task = _named(DETECT_SOCKET, "Reject an unrecognized container runtime")
        assert task, "an unvalidated value is templated into a shell command"
        assert "docker" in str(task["ansible.builtin.fail"]["msg"])

    def test_a_declared_runtime_that_is_absent_fails_the_run(self):
        task = _named(DETECT_SOCKET, "Fail when the declared runtime is not on this host")
        assert task, "falling through to the other runtime is the bug"
        assert "_detected_container_runtime == 'none'" in str(task["when"])


class TestTheChoiceIsVisible:
    def test_create_reports_the_runtime_it_selected(self):
        task = _named(PREFLIGHT, "Report the container runtime this instance will use")
        assert task, (
            "the runtime was only discoverable after the fact, by noticing "
            "docker ps was empty"
        )
        msg = str(task["ansible.builtin.debug"]["msg"])
        assert "podman" in msg and "docker" in msg
        assert "CANASTA_CONTAINER_RUNTIME" in msg, (
            "an operator must be able to tell a declaration from a detection"
        )


class TestTheEnvironmentVariableReachesAnsible:
    """CANASTA_CONTAINER_RUNTIME must arrive as an extra-var, or be refused."""

    def _extra_vars(self, tmp_path, monkeypatch, value=None):
        import json
        import sys

        sys.path.insert(0, REPO_ROOT)
        import canasta

        cfg = tmp_path / "cfg"
        inst = tmp_path / "inst"
        cfg.mkdir()
        inst.mkdir()
        (cfg / "conf.json").write_text(json.dumps({"Instances": {"demo": {
            "path": str(inst), "orchestrator": "compose", "host": "localhost",
        }}}))
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(cfg))
        monkeypatch.delenv("CANASTA_CONTAINER_RUNTIME", raising=False)
        if value is not None:
            monkeypatch.setenv("CANASTA_CONTAINER_RUNTIME", value)

        class _A:
            def __getattr__(self, name):
                return None
        args = _A()
        args.verbose = False
        args.id = "demo"
        with open(os.path.join(REPO_ROOT, "meta",
                               "command_definitions.yml")) as fh:
            definitions = yaml.safe_load(fh)
        argv = canasta.build_ansible_args(
            "ansible-playbook", "reconcile", args, definitions)
        with open(argv[argv.index("-e") + 1].lstrip("@")) as fh:
            return json.load(fh)

    def test_a_declared_runtime_is_passed_through(self, tmp_path, monkeypatch):
        got = self._extra_vars(tmp_path, monkeypatch, "podman")
        assert got["container_runtime"] == "podman"

    def test_unset_passes_nothing(self, tmp_path, monkeypatch):
        # Absent means "probe", not "docker" — the probe still has to run.
        assert "container_runtime" not in self._extra_vars(tmp_path, monkeypatch)

    def test_an_unrecognized_value_is_refused_before_ansible_runs(
            self, tmp_path, monkeypatch):
        import pytest
        with pytest.raises(SystemExit):
            self._extra_vars(tmp_path, monkeypatch, "containerd")
