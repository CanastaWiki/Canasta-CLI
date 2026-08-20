"""A running web container is not a converged instance.

`canasta start` and `canasta reconcile` decided whether to converge by
looking at the web container alone, so a service whose profile was added
since the last converge — a feature just enabled, or COMPOSE_PROFILES
repaired — was never started. reconcile then reported config and
containers "in sync" while a service named in COMPOSE_PROFILES was not
running, and only `canasta restart` recovered it, because it runs `down`
first and gives the following `up -d` something to do.
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import direct_commands  # noqa: E402
import direct_commands._helpers  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
START = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _walk(task[key])


def _start_tasks():
    with open(START) as f:
        return list(_walk(yaml.safe_load(f)))


def _by_name(name):
    return next((t for t in _start_tasks() if t.get("name") == name), None)


class TestMissingProfileServices:
    def _inst(self):
        return {"path": "/srv/test", "host": "localhost",
                "orchestrator": "compose"}

    def test_reports_a_service_the_profiles_imply_but_nothing_runs(
            self, monkeypatch):
        captures = iter(["caddy\ndb\nvarnish\nweb\n", "caddy\ndb\nweb\n"])
        monkeypatch.setattr(
            direct_commands._helpers, "_capture_in_instance",
            lambda *a, **kw: next(captures))
        missing = direct_commands._helpers._missing_profile_services(
            self._inst(), ["docker", "compose"])
        assert missing == ["varnish"]

    def test_reports_nothing_when_converged(self, monkeypatch):
        captures = iter(["caddy\ndb\nweb\n", "web\ncaddy\ndb\n"])
        monkeypatch.setattr(
            direct_commands._helpers, "_capture_in_instance",
            lambda *a, **kw: next(captures))
        assert direct_commands._helpers._missing_profile_services(
            self._inst(), ["docker", "compose"]) == []

    def test_an_unreadable_service_list_does_not_force_a_converge(
            self, monkeypatch):
        # Falling back to the old web-only behavior beats bouncing
        # containers because a ps call failed.
        monkeypatch.setattr(
            direct_commands._helpers, "_capture_in_instance",
            lambda *a, **kw: None)
        assert direct_commands._helpers._missing_profile_services(
            self._inst(), ["docker", "compose"]) == []


class TestStartConvergesWhenAServiceIsMissing:
    def _patch(self, monkeypatch, missing):
        monkeypatch.setattr(
            direct_commands._helpers, "_resolve_instance",
            lambda args: ("test", {"path": "/srv/test", "host": "localhost",
                                   "orchestrator": "compose"}))
        monkeypatch.setattr(
            direct_commands._helpers, "_instance_has_sidecars",
            lambda inst: False)
        monkeypatch.setattr(
            direct_commands._helpers, "_resolve_compose_cmd",
            lambda inst: ["docker", "compose"])
        monkeypatch.setattr(
            direct_commands._helpers, "_check_running_compose",
            lambda *a, **kw: True)
        monkeypatch.setattr(
            direct_commands._helpers, "_sync_compose_profiles",
            lambda inst: {})
        monkeypatch.setattr(
            direct_commands._helpers, "_missing_db_password",
            lambda inst, env: False)
        monkeypatch.setattr(
            direct_commands._helpers, "_missing_profile_services",
            lambda inst, compose_cmd=None: missing)
        calls = []
        monkeypatch.setattr(
            direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, args, **kw: calls.append(args) or 0)
        monkeypatch.setattr(
            direct_commands._helpers, "_wait_web_ready",
            lambda inst_id, inst: 0)
        return calls

    def test_up_is_invoked_for_the_missing_service(self, monkeypatch):
        calls = self._patch(monkeypatch, ["varnish"])
        args = type("Args", (), {"id": "test"})()
        assert direct_commands.cmd_start(args) == 0
        assert calls == [["up", "-d"]], (
            "a service named in COMPOSE_PROFILES but not running must be "
            "started, not reported as already running")

    def test_it_says_what_it_is_starting(self, monkeypatch, capsys):
        self._patch(monkeypatch, ["varnish"])
        direct_commands.cmd_start(type("Args", (), {"id": "test"})())
        assert "Starting varnish" in capsys.readouterr().out

    def test_a_converged_instance_is_still_a_noop(self, monkeypatch):
        calls = self._patch(monkeypatch, [])
        assert direct_commands.cmd_start(
            type("Args", (), {"id": "test"})()) == 0
        assert calls == [], "a converged instance must not bounce containers"


class TestAnsibleStartGuard:
    def test_the_skip_requires_nothing_missing(self):
        skip = _by_name("Skip start when every expected service is running")
        assert skip is not None, "skip task missing/renamed"
        assert "_missing_services | length == 0" in str(skip.get("when"))

    def test_the_converge_runs_when_a_service_is_missing(self):
        up = _by_name("Start containers")
        assert "_missing_services | length > 0" in str(up.get("when")), (
            "up -d is the only step that starts a newly-profiled service")

    def test_expected_services_come_from_compose(self):
        listing = _by_name("List the services the active profiles imply")
        assert listing is not None
        cmd = listing["ansible.builtin.command"]["cmd"]
        assert "config --services" in cmd, (
            "ask compose which services the active profiles imply rather "
            "than duplicating the profile map")
