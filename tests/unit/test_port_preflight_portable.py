"""The port-conflict preflight must work off Linux, and must not treat
a check it could not run as a clean result.

It probed with `ss`, which is iproute2 and absent on macOS. Paired with
`failed_when: false` that produced empty stdout, which the gate below
read as "port is free" — so on a Mac the guard passed silently while
ports 80/443 were demonstrably held, and the conflict surfaced later as
an opaque Docker bind error. macOS localhost Compose is the frontend
development workflow, so that is the case the guard exists for.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PREFLIGHT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml")

_SENTINEL = "CANASTA_PORT_PROBE_UNAVAILABLE"


def _read():
    with open(PREFLIGHT) as f:
        return f.read()


def _tasks():
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    walk(yaml.safe_load(_read()))
    return out


def _probe_tasks():
    return [
        t for t in _tasks()
        if isinstance(t.get("name"), str)
        and t["name"].startswith("Probe for userspace listeners")
    ]


class TestProbeIsPortable:
    def test_one_probe_covers_both_ports(self):
        assert len(_probe_tasks()) == 1

    def test_probe_uses_the_resolved_ports_not_the_defaults(self):
        # A second Compose instance on one host moves off 80/443 via the
        # -e envfile. Hardcoding the defaults here rejected that create
        # before the envfile was ever read.
        cmd = _probe_tasks()[0]["ansible.builtin.script"]["cmd"]
        assert "_preflight_http_port" in cmd
        assert "_preflight_https_port" in cmd

    def test_probe_runs_the_shared_script(self):
        cmd = _probe_tasks()[0]["ansible.builtin.script"]["cmd"]
        assert "port_listeners.sh" in cmd
        assert "canasta_port_probe_image" in cmd


class TestFailureGateIgnoresTheSentinel:
    def _fail_tasks(self):
        return [
            t for t in _tasks()
            if isinstance(t.get("name"), str)
            and re.match(r"Fail if the instance's ports are taken", t["name"])
        ]

    def test_the_gate_exists(self):
        # One gate covers both ports: the HAProxy edge needs both.
        assert len(self._fail_tasks()) == 1

    def test_sentinel_does_not_trip_the_failure(self):
        for task in self._fail_tasks():
            when = task.get("when")
            when_text = " ".join(when) if isinstance(when, list) else str(when)
            assert _SENTINEL in when_text, (
                "%s would abort the create when the probe merely could not "
                "run — the sentinel must be excluded" % task["name"]
            )

    def test_a_missing_probe_is_reported_not_swallowed(self):
        names = [t.get("name") for t in _tasks() if isinstance(t.get("name"), str)]
        assert any("port check could not run" in n for n in names), (
            "nothing tells the operator the check was skipped; a guard that "
            "silently does nothing is worse than no guard"
        )


class TestPortsComeFromTheEnvFile:
    """The documented way to run a second Compose instance on one host is
    `canasta create -e <file>` with HTTP_PORT/HTTPS_PORT. Preflight runs
    long before the envfile is merged into .env, so it has to read the
    controller-side file itself or it rejects that create on the ports
    the instance was never going to bind.
    """

    def _named(self, needle):
        return [
            t for t in _tasks()
            if isinstance(t.get("name"), str) and needle in t["name"]
        ]

    def test_envfile_is_read_on_the_controller(self):
        tasks = self._named("custom env file for port overrides")
        assert tasks, "preflight never reads the -e envfile"
        task = tasks[0]
        assert task.get("delegate_to") == "canasta_controller", (
            "envfile is a controller-side path; slurping it on the target "
            "looks for the controller's path on the remote filesystem")

    def test_ports_are_resolved_with_defaults(self):
        tasks = self._named("Resolve the ports this instance will bind")
        assert tasks, "preflight never resolves the ports to probe"
        facts = tasks[0]["ansible.builtin.set_fact"]
        assert "HTTP_PORT=80" in facts["_preflight_http_port"], (
            "no default; an absent envfile must still probe 80")
        assert "HTTPS_PORT=443" in facts["_preflight_https_port"], (
            "no default; an absent envfile must still probe 443")

    def test_k3s_gate_only_fires_for_the_traefik_ports(self):
        tasks = self._named("Fail if k3s is running")
        assert tasks, "k3s gate disappeared"
        when = tasks[0].get("when")
        when_text = " ".join(when) if isinstance(when, list) else str(when)
        assert "_preflight_http_port" in when_text, (
            "Traefik claims 80 and 443 only; an instance binding neither "
            "must not be blocked by a running k3s")
