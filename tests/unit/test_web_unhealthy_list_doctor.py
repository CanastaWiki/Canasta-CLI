"""list and doctor notice a web container that is crash-looping or unhealthy.

A crash-looping Compose web container is still listed by `ps`, and a
crash-looping pod is intermittently ready, so list read such an instance as
RUNNING (or flipped between RUNNING and STOPPED on Kubernetes), and doctor
said nothing about it. list now shows UNHEALTHY from the same single query
it already made; doctor names the state and restart count and, from web's
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


class TestComposeWebState:
    @pytest.mark.parametrize("ps_out,state", [
        (RESTARTING, "UNHEALTHY"),
        (UNHEALTHY, "UNHEALTHY"),
        (HEALTHY, "RUNNING"),
        (STARTING, "RUNNING"),
        (PODMAN_UP, "RUNNING"),
        ("abc123\n", "RUNNING"),
        # Briefly up between crashes: restarted long after it was created.
        ("abc123|running|Up 2 seconds (health: starting)|12 minutes ago",
         "UNHEALTHY"),
        ("abc123|running|Up Less than a second (starting)|About an hour ago",
         "UNHEALTHY"),
        ("abc123|running|Up 1 second|3 weeks ago", "UNHEALTHY"),
        # Created by this start, and still starting.
        ("abc123|running|Up 2 minutes (health: starting)|2 minutes ago",
         "RUNNING"),
        ("abc123|running|Up 3 seconds|4 seconds ago", "RUNNING"),
        # Restarted in place a while ago and settled.
        ("abc123|running|Up 5 minutes (healthy)|3 weeks ago", "RUNNING"),
        ("abc123|running|Up 5 minutes|3 weeks ago", "RUNNING"),
        ("abc123|running|Up 3 weeks (healthy)|3 weeks ago", "RUNNING"),
        ("abc123|running|Up 59 minutes (healthy)|About an hour ago",
         "RUNNING"),
        ("", "STOPPED"),
        ("\n", "STOPPED"),
    ])
    def test_state(self, ps_out, state):
        assert _helpers._compose_web_state(ps_out) == state

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
        assert "'{{.ID}}|{{.State}}|{{.Status}}|{{.RunningFor}}'" in calls[0]

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


def _pod(name, ready, restarts, reason="", exit_code=""):
    return "%s|%s|%d|%s|%s" % (
        name, "true" if ready else "false", restarts, reason, exit_code)


CRASHLOOP_POD = _pod("canasta-mysite-web-1", False, 14, "CrashLoopBackOff",
                     "123")
READY_POD = _pod("canasta-mysite-web-2", True, 0)


class TestK8sWebState:
    @pytest.mark.parametrize("out,state", [
        (CRASHLOOP_POD, "UNHEALTHY"),
        # Between back-offs the crashed container runs again, not ready.
        (_pod("web-1", False, 3, "", "123"), "UNHEALTHY"),
        (READY_POD, "RUNNING"),
        # A crash-looping pod can pass readiness for a moment.
        (_pod("web-1", True, 14, "", "123"), "RUNNING"),
        # A new pod crash-looping beside an old one that still serves.
        (READY_POD + "\n" + CRASHLOOP_POD, "UNHEALTHY"),
        # Starting for the first time.
        (_pod("web-1", False, 0), "STOPPED"),
        # Pending: no container status yet.
        ("canasta-mysite-web-1|", "STOPPED"),
        ("", "STOPPED"),
    ])
    def test_state(self, out, state):
        assert _helpers._k8s_web_state(_helpers._k8s_web_pods(out)) == state

    def test_remote_is_one_kubectl_call_on_the_host(self, monkeypatch):
        calls = []

        def fake_ssh(host, cmd, **kw):
            calls.append((host, cmd))
            return 0, CRASHLOOP_POD + "\n"

        def no_local(*a, **kw):
            raise AssertionError("must run kubectl on the instance's host")

        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        monkeypatch.setattr(_helpers.subprocess, "run", no_local)
        assert _helpers._web_state_k8s("mysite", "node1") == "UNHEALTHY"
        assert len(calls) == 1
        host, cmd = calls[0]
        assert host == "node1"
        assert cmd.startswith("'kubectl' 'get' 'pods' '-n' 'canasta-mysite' ")
        assert "app.kubernetes.io/component=web" in cmd

    def test_query_failure_reads_as_stopped(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_ssh_run",
                            lambda host, cmd, **kw: (255, ""))
        assert _helpers._web_state_k8s("mysite", "node1") == "STOPPED"

    def test_local_jsonpath_newline_reaches_kubectl_escaped(
            self, monkeypatch):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, READY_POD + "\n", "")

        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)
        assert _helpers._web_state_k8s("mysite", "localhost") == "RUNNING"
        jsonpath = seen["argv"][-1]
        assert jsonpath.startswith("jsonpath=")
        assert '{"\\n"}' in jsonpath


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


K8S_INST = {"id": "mysite", "path": "/srv/mysite", "host": "node1",
            "orchestrator": "kubernetes"}


def _k8s_doctor(monkeypatch, pods_out, logs=MISSING_EXT_LOG):
    calls = []

    def fake_ssh(host, cmd, **kw):
        calls.append(cmd)
        if "'get' 'pods'" in cmd:
            return 0, pods_out
        return 0, logs

    monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
    return doctor._web_health_lines(K8S_INST), calls


class TestDoctorK8s:
    def test_crash_loop_names_pod_restarts_and_cause(self, monkeypatch):
        lines, calls = _k8s_doctor(
            monkeypatch, READY_POD + "\n" + CRASHLOOP_POD + "\n")
        text = "\n".join(lines)
        assert ("pod canasta-mysite-web-1 is crash-looping "
                "(last exit code 123, 14 restarts), so the wiki may not be "
                "served.") in text
        assert ("Cause: MediaWiki can't load extension "
                "'GoogleAnalyticsMetrics'") in text
        assert "Fix: Disable it with" in text
        logs_cmd = calls[1]
        assert "kubectl logs -n 'canasta-mysite'" in logs_cmd
        assert "--previous" in logs_cmd

    def test_no_ready_pod_is_not_being_served(self, monkeypatch):
        lines, _ = _k8s_doctor(monkeypatch, CRASHLOOP_POD + "\n")
        assert ("14 restarts), so the wiki is not being served."
                in "\n".join(lines))

    def test_healthy_prints_nothing_and_fetches_no_logs(self, monkeypatch):
        lines, calls = _k8s_doctor(monkeypatch, READY_POD + "\n")
        assert lines == []
        assert len(calls) == 1


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
