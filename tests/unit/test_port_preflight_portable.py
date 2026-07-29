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
        and re.match(
            r"Probe for userspace listener on the HTTPS? port", t["name"])
    ]


class TestProbeIsPortable:
    def test_both_ports_are_probed(self):
        names = sorted(t["name"] for t in _probe_tasks())
        assert len(names) == 2, (
            "expected an HTTP and an HTTPS probe, got %s" % names)
        assert any("HTTPS port" in n for n in names)
        assert any(n.endswith("the HTTP port") for n in names)

    def test_probes_use_the_resolved_ports_not_the_defaults(self):
        # A second Compose instance on one host moves off 80/443 via the
        # -e envfile. Hardcoding the defaults here rejected that create
        # before the envfile was ever read.
        for task in _probe_tasks():
            cmd = task.get("ansible.builtin.shell", {}).get("cmd", "")
            assert (
                "_preflight_http_port" in cmd
                or "_preflight_https_port" in cmd
            ), (
                "%s probes a literal port; it must probe the port this "
                "instance will actually bind" % task["name"])

    def test_probe_falls_back_when_ss_is_absent(self):
        for task in _probe_tasks():
            cmd = task.get("ansible.builtin.shell", {}).get("cmd", "")
            assert "command -v ss" in cmd, (
                "%s must test for ss rather than assume it" % task["name"])
            assert "lsof" in cmd, (
                "%s has no non-Linux fallback; ss is iproute2-only"
                % task["name"])

    def test_unavailable_probe_is_distinguishable(self):
        # The bug: an unrunnable probe and a free port both produced
        # empty stdout, so they were indistinguishable downstream.
        for task in _probe_tasks():
            cmd = task.get("ansible.builtin.shell", {}).get("cmd", "")
            assert _SENTINEL in cmd, (
                "%s must emit a sentinel when no probe tool exists, or "
                "'could not check' is silently read as 'port is free'"
                % task["name"])


class TestFailureGateIgnoresTheSentinel:
    def _fail_tasks(self):
        return [
            t for t in _tasks()
            if isinstance(t.get("name"), str)
            and re.match(r"Fail if the HTTPS? port is already in use",
                         t["name"])
        ]

    def test_both_gates_exist(self):
        assert len(self._fail_tasks()) == 2

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
        assert task.get("delegate_to") == "localhost", (
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
