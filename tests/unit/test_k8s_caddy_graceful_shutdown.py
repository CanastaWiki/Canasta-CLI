"""The caddy pod must keep serving until the ingress stops routing to it.

A rolling update deletes the old caddy pod once the new one is Ready, and
the ingress keeps sending it requests until the pod's removal from the
caddy Service reaches it. Without a preStop pause Caddy stops at once and
those requests fail with 502s.
"""

import os
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")
CROWDSEC = ("--set", "crowdsec.enabled=true")


def _helm(*args):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    cmd = ["helm", "template", "canasta", CHART,
           "--set", "instance.id=test", "--set", "domains[0]=localhost",
           "--show-only", "templates/deployment-caddy.yaml", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def _render(*args):
    out = _helm(*args)
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)


def _container(doc, name):
    containers = doc["spec"]["template"]["spec"]["containers"]
    return next(c for c in containers if c["name"] == name)


def _prestop(doc, name):
    command = _container(doc, name)["lifecycle"]["preStop"]["exec"]["command"]
    assert command[:2] == ["/bin/sh", "-c"]
    return command[2]


def _grace(doc):
    return doc["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]


@pytest.mark.parametrize("args", [(), CROWDSEC], ids=["plain", "crowdsec"])
class TestCaddyShutdown:
    def test_prestop_sleeps_and_returns(self, args):
        # A plain sleep ends on its own; a hook still running when the
        # container exits is reported as FailedPreStopHook.
        assert _prestop(_render(*args), "caddy") == "sleep 5"

    def test_grace_period_outlasts_the_sleep(self, args):
        assert _grace(_render(*args)) == 30

    def test_replaces_before_removing(self, args):
        assert _render(*args)["spec"]["strategy"] == {
            "type": "RollingUpdate",
            "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}

    def test_values_are_configurable(self, args):
        doc = _render(*args,
                      "--set", "caddy.preStopSleepSeconds=8",
                      "--set", "caddy.terminationGracePeriodSeconds=45",
                      "--set", "caddy.rollingUpdate.maxSurge=2")
        assert _prestop(doc, "caddy") == "sleep 8"
        assert _grace(doc) == 45
        assert doc["spec"]["strategy"]["rollingUpdate"]["maxSurge"] == 2

    def test_grace_period_not_above_the_sleep_is_refused(self, args):
        out = _helm(*args,
                    "--set", "caddy.preStopSleepSeconds=30",
                    "--set", "caddy.terminationGracePeriodSeconds=30")
        assert out.returncode != 0
        assert "caddy.terminationGracePeriodSeconds must be greater" \
            in out.stderr


def test_agent_stops_with_caddy():
    # The agent waits as long as Caddy, so it parses the requests the
    # outgoing pod serves while it drains.
    doc = _render(*CROWDSEC, "--set", "caddy.preStopSleepSeconds=8",
                  "--set", "caddy.terminationGracePeriodSeconds=45")
    assert _prestop(doc, "crowdsec-agent") == _prestop(doc, "caddy")
    assert "readinessProbe" not in _container(doc, "crowdsec-agent")
