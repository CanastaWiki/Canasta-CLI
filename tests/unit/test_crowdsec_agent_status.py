"""On Compose `crowdsec status` and `metrics` report the engine alone.

There is no separate agent, so `status` has no agent line and `metrics`
shows the local API's metrics only.
"""

import datetime
import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

CROWDSEC = os.path.join(REPO_ROOT, "roles", "crowdsec", "tasks")


def _show(path, name, play_vars):
    with open(path) as f:
        task = next(t for t in yaml.safe_load(f) if t.get("name") == name)
    task = dict(task, register="_shown")
    # The msg is written to a file rather than read from a stdout callback:
    # ansible-core ships no json callback, and CI has no collections.
    save = {"ansible.builtin.copy": {"content": "{{ _shown.msg }}",
                                     "dest": "{{ shown_msg_path }}"}}
    play = [{"hosts": "localhost", "connection": "local",
             "gather_facts": False,
             "vars": dict({"ansible_python_interpreter": sys.executable},
                          **play_vars),
             "tasks": [task, save]}]
    return play


def _run(tmp_path, play):
    path = tmp_path / "play.yml"
    shown = tmp_path / "shown.txt"
    play[0]["vars"]["shown_msg_path"] = str(shown)
    path.write_text(yaml.safe_dump(play))
    base = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env = dict(base, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "no-collections"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(path)],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return shown.read_text()


class TestStatusShowsTheAgent:
    def _msg(self, tmp_path, k8s):
        heartbeat = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return _run(tmp_path, _show(
            os.path.join(CROWDSEC, "status.yml"), "Show CrowdSec status", {
                "instance_id": "demo", "instance_is_k8s": k8s,
                "_crowdsec_bouncer_list": [],
                "_crowdsec_bouncer_lines": "  (none)",
                "_crowdsec_capi_line": "registered",
                "_crowdsec_console_line": "none",
                "_crowdsec_decisions": {"stdout": "(none)"},
                "_crowdsec_machines": {"stdout": json.dumps([{
                    "machineId": "canasta-caddy-agent",
                    "last_heartbeat": heartbeat}])},
                "_crowdsec_pod_starts": {"stdout": ""}}))

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

    def test_compose_shows_the_engine(self, tmp_path):
        assert self._msg(tmp_path, False) == (
            "CrowdSec metrics for instance 'demo'\n\nLAPI METRICS")
