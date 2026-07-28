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
        and re.match(r"Probe for userspace listener on port \d+", t["name"])
    ]


class TestProbeIsPortable:
    def test_both_ports_are_probed(self):
        ports = sorted(
            re.search(r"port (\d+)", t["name"]).group(1)
            for t in _probe_tasks()
        )
        assert ports == ["443", "80"], "expected probes for 80 and 443, got %s" % ports

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
            and t["name"].startswith("Fail if port")
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
