"""list and doctor notice a crash-looping web pod on Kubernetes.

A crash-looping pod is intermittently ready, so list reads the web pods'
readiness and restart counts with one kubectl call on the instance's host
and shows UNHEALTHY; doctor names the pod, its last exit code and restart
count and, from the previous container's log, the startup error and its
fix.
"""

import subprocess

import pytest

from test_web_unhealthy_list_doctor import K8S_INST, MISSING_EXT_LOG, _pod

from direct_commands import _helpers  # noqa: E402
from direct_commands import doctor  # noqa: E402


CRASHLOOP_POD = _pod("canasta-mysite-web-1", False, 14, "CrashLoopBackOff",
                     "123")
READY_POD = _pod("canasta-mysite-web-2", True, 0)


def _k8s_doctor(monkeypatch, pods_out, logs=MISSING_EXT_LOG):
    calls = []

    def fake_ssh(host, cmd, **kw):
        calls.append(cmd)
        if "'get' 'pods'" in cmd:
            return 0, pods_out
        return 0, logs

    monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
    return doctor._web_health_lines(K8S_INST), calls


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
