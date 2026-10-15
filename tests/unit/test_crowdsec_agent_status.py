"""On Kubernetes `crowdsec status` and `metrics` cover the agent.

The agent in the caddy pod is the only thing parsing the access log, so a
stopped agent leaves the local API healthy while nothing is detected.
`status` reports the agent's last heartbeat and warns when it stops, and
`metrics` and `scenarios` read the agent, where parsing happens.
"""

import datetime
import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

from canasta_crowdsec import canasta_crowdsec_agent_line  # noqa: E402

CROWDSEC = os.path.join(REPO_ROOT, "roles", "crowdsec", "tasks")


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


def _show(path, name, play_vars):
    with open(path) as f:
        task = next(t for t in yaml.safe_load(f) if t.get("name") == name)
    play = [{"hosts": "localhost", "connection": "local",
             "gather_facts": False,
             "vars": dict({"ansible_python_interpreter": sys.executable},
                          **play_vars),
             "tasks": [task]}]
    return play


def _run(tmp_path, play):
    path = tmp_path / "play.yml"
    path.write_text(yaml.safe_dump(play))
    base = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env = dict(base, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="json",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(path)],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    return result["plays"][0]["tasks"][0]["hosts"]["localhost"]["msg"]


class TestStatusShowsTheAgent:
    def _msg(self, tmp_path, k8s):
        heartbeat = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return _run(tmp_path, _show(
            os.path.join(CROWDSEC, "status.yml"), "Show CrowdSec status", {
                "instance_id": "demo", "instance_is_k8s": k8s,
                "_crowdsec_bouncer_list": [],
                "_crowdsec_capi_line": "registered",
                "_crowdsec_console_line": "none",
                "_crowdsec_decisions": {"stdout": "(none)"},
                "_crowdsec_machines": {"stdout": json.dumps([{
                    "machineId": "canasta-caddy-agent",
                    "last_heartbeat": heartbeat}])}}))

    def test_kubernetes_reports_the_agent(self, tmp_path):
        msg = self._msg(tmp_path, True)
        assert "\nAgent: reporting (last heartbeat " in msg
        assert "\nCentral API (community blocklist): registered" in msg

    def test_compose_has_no_agent_line(self, tmp_path):
        msg = self._msg(tmp_path, False)
        assert "Agent:" not in msg
        assert "\n\nCentral API (community blocklist): registered" in msg


class TestMetricsShowTheAgent:
    def _msg(self, tmp_path, k8s):
        return _run(tmp_path, _show(
            os.path.join(CROWDSEC, "metrics.yml"), "Show metrics", {
                "instance_id": "demo", "instance_is_k8s": k8s,
                "_crowdsec_metrics": {"stdout": "LAPI METRICS"},
                "_crowdsec_agent_metrics": {"stdout": "AGENT METRICS"}}))

    def test_kubernetes_shows_agent_and_local_api(self, tmp_path):
        assert self._msg(tmp_path, True) == (
            "CrowdSec metrics for instance 'demo'\n\n"
            "Agent (caddy pod):\nAGENT METRICS\n\n"
            "Local API:\nLAPI METRICS")

    def test_compose_shows_the_engine(self, tmp_path):
        assert self._msg(tmp_path, False) == (
            "CrowdSec metrics for instance 'demo'\n\nLAPI METRICS")

    def test_metrics_and_scenarios_read_the_agent(self):
        for name in ("metrics.yml", "scenarios.yml"):
            with open(os.path.join(CROWDSEC, name)) as f:
                text = f.read()
            assert "_cscli_target: agent" in text, name
