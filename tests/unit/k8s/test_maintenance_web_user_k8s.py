"""Maintenance scripts on Kubernetes run as the web server's user.

Inside the pod the command is wrapped in `runuser -u`, locally through
kubectl exec and remotely through the ssh exec command, and exec.yml's
Kubernetes command does the same for exec_user; without a user the image's
default is kept.
"""

import subprocess

import yaml

from test_maintenance_web_user import EXEC_YML, _helpers, _render, popen  # noqa: F401


class TestStreamInContainerUser:
    def test_local_k8s_runs_as_user(self, popen, monkeypatch):  # noqa: F811
        monkeypatch.setattr(_helpers, "_k8s_get_pod", lambda ns, svc: "web-1")
        inst = {"host": "localhost", "orchestrator": "kubernetes"}
        _helpers._stream_in_container("mysite", inst, "php -v",
                                      user="www-data")
        assert popen[0].argv == [
            "kubectl", "exec", "-i", "web-1", "-n", "canasta-mysite",
            "-c", "web", "--", "runuser", "-u", "www-data", "--",
            "/bin/bash", "-c", "stdbuf -oL php -v",
        ]

    def test_local_k8s_without_user_keeps_default(self, popen, monkeypatch):  # noqa: F811
        monkeypatch.setattr(_helpers, "_k8s_get_pod", lambda ns, svc: "web-1")
        inst = {"host": "localhost", "orchestrator": "kubernetes"}
        _helpers._stream_in_container("mysite", inst, "php -v")
        assert popen[0].argv == [
            "kubectl", "exec", "-i", "web-1", "-n", "canasta-mysite",
            "-c", "web", "--", "/bin/bash", "-c", "stdbuf -oL php -v",
        ]

    def test_remote_k8s_runs_as_user(self, popen):  # noqa: F811
        inst = {"host": "remote1", "orchestrator": "kubernetes"}
        _helpers._stream_in_container("mysite", inst, "php -v",
                                      user="www-data")
        assert popen[0].argv == [
            "ssh", "remote1",
            _helpers._k8s_remote_exec_cmd("mysite", "web",
                                          "stdbuf -oL php -v",
                                          user="www-data"),
        ]


class TestK8sRemoteExecCmdUser:
    def test_runs_as_user(self):
        cmd = _helpers._k8s_remote_exec_cmd("mysite", "web", "php -v",
                                            user="www-data")
        assert cmd.endswith(
            '-c web -- runuser -u www-data -- /bin/bash -c \'php -v\'')

    def test_without_user_keeps_default(self):
        cmd = _helpers._k8s_remote_exec_cmd("mysite", "web", "php -v")
        assert cmd.endswith('-c web -- /bin/bash -c \'php -v\'')
        assert "runuser" not in cmd


class TestExecInContainerUser:
    def test_local_k8s_runs_as_user(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(_helpers, "_k8s_get_pod", lambda ns, svc: "web-1")

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")
        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)
        inst = {"host": "localhost", "orchestrator": "kubernetes"}
        _helpers._exec_in_container("mysite", inst, "true", user="www-data")
        assert captured["argv"] == [
            "kubectl", "exec", "web-1", "-n", "canasta-mysite", "-c", "web",
            "--", "runuser", "-u", "www-data", "--", "/bin/bash", "-c", "true",
        ]

    def test_remote_k8s_runs_as_user(self, monkeypatch):
        captured = {}

        def fake_ssh(host, cmd, **kw):
            captured["cmd"] = cmd
            return 0, ""
        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        inst = {"host": "remote1", "orchestrator": "kubernetes"}
        _helpers._exec_in_container("mysite", inst, "true", user="www-data")
        assert captured["cmd"] == _helpers._k8s_remote_exec_cmd(
            "mysite", "web", "true", forward_stdin=False, user="www-data")
        assert "runuser -u www-data --" in captured["cmd"]


def _render_k8s_exec(extra):
    with open(EXEC_YML) as f:
        tasks = yaml.safe_load(f)
    block = next(t for t in tasks
                 if t.get("name") == "Resolve K8s pod and build exec command")
    build = next(t for t in block["block"]
                 if t.get("name") == "Build K8s exec command")
    template = build["ansible.builtin.set_fact"]["_exec_full_cmd"]
    return _render(template, {"_k8s_pod_name": "web-1",
                              "_k8s_namespace": "canasta-mysite", **extra})


class TestExecYmlUser:
    def test_k8s_exec_runs_as_exec_user(self):
        assert _render_k8s_exec({"exec_user": "www-data"}) == (
            "kubectl exec web-1 -n canasta-mysite -c web -- "
            "runuser -u www-data -- sh 'php x.php'")

    def test_k8s_exec_defaults_to_image_user(self):
        assert _render_k8s_exec({}) == (
            "kubectl exec web-1 -n canasta-mysite -c web -- sh 'php x.php'")
