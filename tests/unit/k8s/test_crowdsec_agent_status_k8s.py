"""On Kubernetes `crowdsec status` and `metrics` cover the agent.

The agent in the caddy pod is the only thing parsing the access log, so a
stopped agent leaves the local API healthy while nothing is detected.
`status` reports the agent's last heartbeat and warns when it stops, and
`metrics` and `scenarios` read the agent, where parsing happens.
"""

import datetime
import json
import os
import sys

import test_crowdsec_agent_status as shared
from test_crowdsec_agent_status import CROWDSEC, REPO_ROOT, _run, _show

sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

from canasta_crowdsec import canasta_crowdsec_agent_line  # noqa: E402


class TestAgentStatusLine:
    NOW = datetime.datetime(2026, 10, 15, 12, 0,
                            tzinfo=datetime.timezone.utc)

    def _line(self, machines):
        import json
        return canasta_crowdsec_agent_line(
            json.dumps(machines), "canasta-caddy-agent", now=self.NOW)

    def test_recent_heartbeat_is_reporting(self):
        line = self._line([{"machineId": "canasta-caddy-agent",
                            "last_heartbeat": "2026-10-15T11:59:30.123456789Z"}])
        assert line == "reporting (last heartbeat 2026-10-15 11:59 UTC)"

    def test_stale_heartbeat_warns(self):
        line = self._line([{"machineId": "canasta-caddy-agent",
                            "last_heartbeat": "2026-10-15T11:40:00+00:00"}])
        assert line.startswith("WARNING: no report since 2026-10-15 11:40 UTC")

    def test_never_reported_warns(self):
        line = self._line([{"machineId": "canasta-caddy-agent",
                            "last_heartbeat": None}])
        assert "never reported" in line

    def test_missing_agent_warns(self):
        for machines in [None, [], [{"machineId": "localhost"}]]:
            assert "not registered" in self._line(machines)

    def _starting(self, machines, started_at):
        return canasta_crowdsec_agent_line(
            json.dumps(machines), "canasta-caddy-agent",
            started_at, now=self.NOW)

    def test_new_pod_without_a_report_is_starting(self):
        never = [{"machineId": "canasta-caddy-agent", "last_heartbeat": None}]
        line = self._starting(never, "2026-10-15T11:59:30Z")
        assert line == ("starting (pods restarted 30 s ago; the agent "
                        "reports once it has logged in)")

    def test_newest_pod_start_counts(self):
        stale = [{"machineId": "canasta-caddy-agent",
                  "last_heartbeat": "2026-10-15T11:00:00Z"}]
        line = self._starting(
            stale, "2026-10-15T10:00:00Z 2026-10-15T11:58:00Z")
        assert line.startswith("starting (pods restarted 120 s ago")

    def test_unregistered_agent_is_starting_after_a_restart(self):
        assert self._starting([], "2026-10-15T11:59:00Z").startswith(
            "starting")

    def test_warns_once_the_grace_period_is_over(self):
        never = [{"machineId": "canasta-caddy-agent", "last_heartbeat": None}]
        assert "never reported" in self._starting(
            never, "2026-10-15T11:56:59Z")

    def test_reporting_agent_ignores_pod_age(self):
        live = [{"machineId": "canasta-caddy-agent",
                 "last_heartbeat": "2026-10-15T11:59:50Z"}]
        assert self._starting(live, "2026-10-15T11:59:55Z").startswith(
            "reporting")


class TestStatusShowsTheAgent:
    _msg = shared.TestStatusShowsTheAgent._msg

    def test_kubernetes_new_pod_reports_starting(self, tmp_path):
        started = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        msg = _run(tmp_path, _show(
            os.path.join(CROWDSEC, "status.yml"), "Show CrowdSec status", {
                "instance_id": "demo", "instance_is_k8s": True,
                "_crowdsec_bouncer_list": [],
                "_crowdsec_bouncer_lines": "  (none)",
                "_crowdsec_capi_line": "registered",
                "_crowdsec_console_line": "none",
                "_crowdsec_decisions": {"stdout": "(none)"},
                "_crowdsec_machines": {"stdout": json.dumps([{
                    "machineId": "canasta-caddy-agent",
                    "last_heartbeat": None}])},
                "_crowdsec_pod_starts": {"stdout": started}}))
        assert "\nAgent: starting (pods restarted " in msg

    def test_kubernetes_reports_the_agent(self, tmp_path):
        msg = self._msg(tmp_path, True)
        assert "\nAgent: reporting (last heartbeat " in msg
        assert "\nCentral API (community blocklist): registered" in msg


class TestMetricsShowTheAgent:
    _msg = shared.TestMetricsShowTheAgent._msg

    def test_kubernetes_shows_agent_and_local_api(self, tmp_path):
        assert self._msg(tmp_path, True) == (
            "CrowdSec metrics for instance 'demo'\n\n"
            "Agent (caddy pod):\nAGENT METRICS\n\n"
            "Local API:\nLAPI METRICS")

    def test_kubernetes_agent_with_nothing_parsed_yet(self, tmp_path):
        msg = _run(tmp_path, _show(
            os.path.join(CROWDSEC, "metrics.yml"), "Show metrics", {
                "instance_id": "demo", "instance_is_k8s": True,
                "_crowdsec_metrics": {"stdout": "LAPI METRICS"},
                "_crowdsec_agent_metrics": {"stdout": "", "stderr": ""}}))
        assert ("Agent (caddy pod):\n(none yet: the agent reports metrics "
                "once it has parsed lines from the Caddy access log)\n") in msg

    def test_metrics_and_scenarios_read_the_agent(self):
        for name in ("metrics.yml", "scenarios.yml"):
            with open(os.path.join(CROWDSEC, name)) as f:
                text = f.read()
            assert "_cscli_target: agent" in text, name
