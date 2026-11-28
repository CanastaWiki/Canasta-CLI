"""list and doctor notice a web container that is crash-looping or unhealthy.

A crash-looping web container is still listed by `ps`, so list must not read
such an instance as RUNNING. list shows UNHEALTHY from the same single query
it already makes; doctor names the state and restart count and, from web's
log, the startup error and its fix.
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import _helpers, doctor  # noqa: E402

MISSING_EXT_LOG = (
    "[Wed Oct 14 12:00:00 2026] PHP Fatal error: Uncaught Exception: "
    "Unable to open file /var/www/mediawiki/w/extensions/GoogleAnalyticsMetrics"
    "/extension.json\n"
    "Error: The GoogleAnalyticsMetrics extension cannot be loaded. Check that "
    "all of its files are installed properly.\n"
    "Unsupported database type\n"
)

RESTARTING = "abc123|restarting|Restarting (123) 5 seconds ago"
UNHEALTHY = "abc123|running|Up 7 minutes (unhealthy)"
HEALTHY = "abc123|running|Up 7 minutes (healthy)"
STARTING = "abc123|running|Up 10 seconds (health: starting)"
PODMAN_UP = "abc123|running|Up 3 minutes"


UP_BETWEEN_CRASHES = "abc123|running|Up 2 seconds (health: starting)"


def _never_asked(cid):
    raise AssertionError("a settled container must not be inspected")


class TestComposeWebState:
    @pytest.mark.parametrize("ps_out,state", [
        (RESTARTING, "UNHEALTHY"),
        (UNHEALTHY, "UNHEALTHY"),
        (HEALTHY, "RUNNING"),
        (PODMAN_UP, "RUNNING"),
        ("abc123|running|Up 5 seconds (healthy)", "RUNNING"),
        ("abc123\n", "RUNNING"),
        ("", "STOPPED"),
        ("\n", "STOPPED"),
    ])
    def test_settled_needs_no_restart_count(self, ps_out, state):
        assert _helpers._compose_web_state(ps_out, _never_asked) == state

    @pytest.mark.parametrize("ps_out", [
        STARTING,
        UP_BETWEEN_CRASHES,
        "abc123|running|Up Less than a second (starting)",
        "abc123|running|Up 1 second",
        "abc123|running|Up 59 seconds",
        "abc123|running|Up 3 seconds ago",
    ])
    @pytest.mark.parametrize("count,state", [
        (3, "UNHEALTHY"),
        # start and restart reset the count, as a daemon restart does.
        (0, "RUNNING"),
        (None, "RUNNING"),
    ])
    def test_starting_reads_the_restart_count(self, ps_out, count, state):
        asked = []

        def restart_count(cid):
            asked.append(cid)
            return count

        assert _helpers._compose_web_state(ps_out, restart_count) == state
        assert asked == ["abc123"]

    def test_local_list_reads_restarting_as_unhealthy(
            self, tmp_path, monkeypatch):
        (tmp_path / "config").mkdir()
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, RESTARTING + "\n", "")

        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)
        inst = {"id": "mysite", "path": str(tmp_path),
                "orchestrator": "compose"}
        detail = direct_commands._gather_instance_info("mysite", inst)
        assert detail["status"] == "UNHEALTHY"
        assert len(calls) == 1
        assert calls[0][1] == "ps"
        assert "label=com.docker.compose.service=web" in calls[0]

    @pytest.mark.parametrize("count,state", [
        ("3", "UNHEALTHY"), ("0", "RUNNING")])
    def test_local_list_inspects_only_a_starting_container(
            self, tmp_path, monkeypatch, count, state):
        (tmp_path / "config").mkdir()
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            out = UP_BETWEEN_CRASHES if argv[1] == "ps" else count
            return subprocess.CompletedProcess(argv, 0, out + "\n", "")

        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)
        inst = {"id": "mysite", "path": str(tmp_path),
                "orchestrator": "compose"}
        detail = direct_commands._gather_instance_info("mysite", inst)
        assert detail["status"] == state
        assert [c[1] for c in calls] == ["ps", "inspect"]
        assert calls[1][-3:] == ["--format", "{{.RestartCount}}", "abc123"]

    def test_remote_list_reads_unhealthy_from_the_batched_call(
            self, monkeypatch):
        calls = []
        out = ("DIR_OK\n" + _helpers._SENTINEL + "\n"
               + "WIKIS_MISSING\n" + _helpers._SENTINEL + "\n"
               + UNHEALTHY + "\n" + _helpers._SENTINEL + "\n")

        def fake_ssh(host, cmd, **kw):
            calls.append(cmd)
            return 0, out

        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        inst = {"id": "site", "path": "/srv/site", "orchestrator": "compose",
                "host": "remote"}
        detail = direct_commands._gather_instance_info("site", inst)
        assert detail["status"] == "UNHEALTHY"
        assert len(calls) == 1
        # The format's pipes must reach ps, not the shell.
        assert "'{{.ID}}|{{.State}}|{{.Status}}'" in calls[0]


FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$CALLS"
case "$1" in
  ps) cat "$PS_OUT" ;;
  inspect) eval "echo \\$COUNT_$4" ;;
esac
"""


class TestRemoteListScript:
    """Run the batched remote script under sh with a stand-in docker, so
    the shell's choice of which containers to inspect is exercised."""

    def _run(self, tmp_path, monkeypatch, ps_lines, counts):
        bindir = tmp_path / "bin"
        bindir.mkdir()
        docker = bindir / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        (tmp_path / "ps").write_text("".join(l + "\n" for l in ps_lines))
        site = tmp_path / "site"
        (site / "config").mkdir(parents=True)
        env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]),
                   PS_OUT=str(tmp_path / "ps"),
                   CALLS=str(tmp_path / "calls"))
        for cid, count in counts.items():
            env["COUNT_" + cid] = str(count)

        def fake_ssh(host, cmd, **kw):
            r = subprocess.run(["sh", "-c", cmd], capture_output=True,
                               text=True, env=env)
            return r.returncode, r.stdout

        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        inst = {"id": "site", "path": str(site), "orchestrator": "compose",
                "host": "remote"}
        detail = direct_commands._gather_instance_info("site", inst)
        calls = (tmp_path / "calls").read_text().splitlines()
        return detail["status"], calls

    @pytest.mark.parametrize("count,state", [(4, "UNHEALTHY"), (0, "RUNNING")])
    def test_starting_container_is_inspected(
            self, tmp_path, monkeypatch, count, state):
        status, calls = self._run(tmp_path, monkeypatch,
                                  [UP_BETWEEN_CRASHES], {"abc123": count})
        assert status == state
        assert calls[1] == "inspect --format {{.RestartCount}} abc123"

    @pytest.mark.parametrize("line", [
        "abc123|running|Up Less than a second",
        "abc123|running|Up 12 seconds",
        "abc123|running|Up 1 second (starting)",
    ])
    def test_other_starting_statuses_are_inspected(
            self, tmp_path, monkeypatch, line):
        status, calls = self._run(tmp_path, monkeypatch, [line],
                                  {"abc123": 2})
        assert status == "UNHEALTHY"
        assert len(calls) == 2

    @pytest.mark.parametrize("lines,state", [
        ([HEALTHY], "RUNNING"),
        ([PODMAN_UP], "RUNNING"),
        ([RESTARTING], "UNHEALTHY"),
        ([], "STOPPED"),
    ])
    def test_settled_container_is_not_inspected(
            self, tmp_path, monkeypatch, lines, state):
        status, calls = self._run(tmp_path, monkeypatch, lines,
                                  {"abc123": 5})
        assert status == state
        assert [c.split()[0] for c in calls] == ["ps"]


class TestListOutput:
    def test_list_prints_unhealthy(self, tmp_path, monkeypatch, capsys):
        site = tmp_path / "site"
        (site / "config").mkdir(parents=True)
        monkeypatch.setattr(_helpers, "_read_registry", lambda p: {
            "site": {"id": "site", "path": str(site),
                     "orchestrator": "compose"}})
        monkeypatch.setattr(
            _helpers.subprocess, "run",
            lambda argv, **kw: subprocess.CompletedProcess(
                argv, 0, RESTARTING + "\n", ""))
        args = type("Args", (), {"cleanup": False, "host": None})()
        assert direct_commands.cmd_list(args) == 0
        assert "UNHEALTHY" in capsys.readouterr().out


COMPOSE_INST = {"id": "mysite", "path": "/srv/mysite", "host": "localhost",
                "orchestrator": "compose"}


def _compose_doctor(monkeypatch, inspect_out, logs=MISSING_EXT_LOG,
                    cid="abc123"):
    fetched = []

    def web_logs(inst, tail):
        fetched.append(tail)
        return logs

    monkeypatch.setattr(_helpers, "_web_container_id", lambda inst: cid)
    monkeypatch.setattr(_helpers, "_resolve_inspect_cmd", lambda inst: "docker")
    monkeypatch.setattr(_helpers, "_runtime_capture",
                        lambda inst, argv, timeout=30: (0, inspect_out))
    monkeypatch.setattr(_helpers, "_web_logs", web_logs)
    monkeypatch.setattr(_helpers, "_read_instance_file", lambda *a: "")
    return doctor._web_health_lines(COMPOSE_INST), fetched


class TestDoctorCompose:
    def test_crash_loop_names_state_restarts_and_cause(self, monkeypatch):
        lines, fetched = _compose_doctor(monkeypatch, "restarting|14||123\n")
        text = "\n".join(lines)
        assert "Web container (mysite):" in text
        assert "WARN" in text
        assert ("crash-looping (last exit code 123, 14 restarts), so the "
                "wiki is not being served.") in text
        assert ("Cause: MediaWiki can't load extension "
                "'GoogleAnalyticsMetrics'") in text
        assert ("Fix: Disable it with 'canasta extension disable "
                "GoogleAnalyticsMetrics -i mysite'") in text
        assert fetched == [_helpers._WEB_CAUSE_SCAN_LINES]

    def test_failing_health_check_is_reported(self, monkeypatch):
        lines, _ = _compose_doctor(monkeypatch, "running|0|unhealthy|0\n")
        text = "\n".join(lines)
        assert ("failing its health check (0 restarts), so the wiki may "
                "not be served.") in text
        assert "Cause:" in text

    def test_up_between_crashes_is_reported(self, monkeypatch):
        lines, _ = _compose_doctor(monkeypatch, "running|3|starting|0\n")
        text = "\n".join(lines)
        assert ("starting again after 3 restarts, so the wiki may not be "
                "served.") in text
        assert "Cause:" in text

    def test_unrecognized_log_shows_its_tail(self, monkeypatch):
        log = "\n".join("line %d" % i for i in range(30))
        lines, _ = _compose_doctor(monkeypatch, "restarting|1||1\n", logs=log)
        text = "\n".join(lines)
        assert "1 restart)" in text
        assert "No known startup error" in text
        assert "    line 29" in text
        assert "line 19" not in text

    @pytest.mark.parametrize("inspect_out", [
        "running|0|healthy|0\n",
        "running|0|starting|0\n",
        # An image with no healthcheck.
        "running|2||0\n",
    ])
    def test_healthy_prints_nothing_and_fetches_no_logs(
            self, monkeypatch, inspect_out):
        lines, fetched = _compose_doctor(monkeypatch, inspect_out)
        assert lines == []
        assert fetched == []

    def test_stopped_prints_nothing(self, monkeypatch):
        lines, fetched = _compose_doctor(monkeypatch, "", cid="")
        assert lines == []
        assert fetched == []


class TestDoctorWiring:
    def test_doctor_prints_the_section_for_the_instance(
            self, monkeypatch, capsys):
        inst = dict(COMPOSE_INST)
        monkeypatch.setattr(_helpers, "_get_config_dir", lambda: "/nonexistent")
        monkeypatch.setattr(_helpers, "_read_registry",
                            lambda path: {"mysite": inst})
        monkeypatch.setattr(doctor, "_collection_integrity_lines", lambda: [])
        monkeypatch.setattr(doctor, "_instance_consistency_lines",
                            lambda i: [])
        monkeypatch.setattr(doctor, "_db_tuning_lines", lambda i: [])
        monkeypatch.setattr(doctor, "_origin_tls_lines", lambda i: [])
        monkeypatch.setattr(doctor, "_composer_failure_lines", lambda i: [])
        monkeypatch.setattr(doctor, "_web_health_lines",
                            lambda i: ["", "WEB-SECTION %s" % i["id"]])
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
        args = type("Args", (), {"host": None, "id": "mysite"})()
        doctor.cmd_doctor(args)
        assert "WEB-SECTION mysite" in capsys.readouterr().out
