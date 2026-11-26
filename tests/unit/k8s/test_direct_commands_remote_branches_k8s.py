"""The Kubernetes branches of the direct-command helpers, gitops status and
gitops diff: the running-pod lookup, exec and streaming into a pod locally
and over ssh, instance gathering for `canasta list`, the gitops status that
skips the git-crypt check, and the gitops diff that falls back to Ansible."""

import subprocess

import pytest

from test_direct_commands_remote_branches import (  # noqa: F401
    _Completed,
    _FakePopen,
    _SshRecorder,
    _args,
    _isolated_ssh,
)

import direct_commands  # noqa: E402
from direct_commands import _helpers, gitops  # noqa: E402


class TestK8sGetPod:
    def test_returns_running_pod(self, monkeypatch):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return _Completed(0, "web-abc123\n")
        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)
        assert _helpers._k8s_get_pod("canasta-mysite", "db") == "web-abc123"
        assert calls[0] == [
            "kubectl", "get", "pods", "-n", "canasta-mysite",
            "-l", "app.kubernetes.io/component=db",
            "--field-selector=status.phase=Running",
            "-o", _helpers.K8S_POD_JSONPATH,
        ]

    def test_skips_a_pod_being_deleted(self, monkeypatch):
        monkeypatch.setattr(
            _helpers.subprocess, "run", lambda argv, **kw: _Completed(
                0, "web-old 2026-10-16T01:20:00Z\nweb-new \n"))
        assert _helpers._k8s_get_pod("canasta-mysite") == "web-new"

    def test_none_when_every_pod_is_being_deleted(self, monkeypatch):
        monkeypatch.setattr(
            _helpers.subprocess, "run", lambda argv, **kw: _Completed(
                0, "web-old 2026-10-16T01:20:00Z\n"))
        assert _helpers._k8s_get_pod("canasta-mysite") is None

    @pytest.mark.parametrize("result", [
        _Completed(0, ""), _Completed(1, "web-abc\n")])
    def test_none_when_empty_or_failed(self, monkeypatch, result):
        monkeypatch.setattr(_helpers.subprocess, "run",
                            lambda argv, **kw: result)
        assert _helpers._k8s_get_pod("canasta-mysite") is None

    @pytest.mark.parametrize("exc", [
        subprocess.TimeoutExpired("kubectl", 10), OSError("no kubectl")])
    def test_none_on_exception(self, monkeypatch, exc):
        def boom(argv, **kw):
            raise exc
        monkeypatch.setattr(_helpers.subprocess, "run", boom)
        assert _helpers._k8s_get_pod("canasta-mysite") is None


class TestExecInContainerRemote:
    def test_remote_k8s_runs_lookup_and_exec_on_host(self, monkeypatch):
        ssh = _SshRecorder((0, "v"))
        monkeypatch.setattr(_helpers, "_ssh_run", ssh)
        inst = {"host": "remote1", "path": "/srv/x",
                "orchestrator": "kubernetes"}
        assert _helpers._exec_in_container(
            "mysite", inst, "php -v") == (0, "v")
        cmd = ssh.calls[0][1]
        assert "kubectl get pods -n canasta-mysite" in cmd
        assert ('exec kubectl exec "$pod" -n canasta-mysite -c web -- '
                "/bin/bash -c 'php -v'") in cmd

    def test_local_k8s_without_pod(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_k8s_get_pod", lambda ns, svc: None)
        inst = {"host": "localhost", "orchestrator": "kubernetes"}
        assert _helpers._exec_in_container("mysite", inst, "x") == (1, "")

    def test_local_k8s_execs_in_pod(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_k8s_get_pod",
                            lambda ns, svc: "web-1")
        calls = []
        monkeypatch.setattr(
            _helpers.subprocess, "run",
            lambda argv, **kw: calls.append(argv) or _Completed(0, "ok"))
        inst = {"host": "localhost", "orchestrator": "k8s"}
        assert _helpers._exec_in_container("mysite", inst, "x") == (0, "ok")
        assert calls[0] == ["kubectl", "exec", "web-1", "-n",
                            "canasta-mysite", "-c", "web", "--",
                            "/bin/bash", "-c", "x"]


class TestStreamInContainer:
    @pytest.fixture(autouse=True)
    def _popen(self, monkeypatch):
        _FakePopen.instances = []
        _FakePopen.returncodes = []
        monkeypatch.setattr(_helpers.subprocess, "Popen", _FakePopen)

    def test_local_k8s_streams_from_pod(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_k8s_get_pod",
                            lambda ns, svc: "web-1")
        inst = {"host": "localhost", "orchestrator": "kubernetes"}
        assert _helpers._stream_in_container("mysite", inst, "php -v") == 0
        assert _FakePopen.instances[0].argv == [
            "kubectl", "exec", "-i", "web-1", "-n", "canasta-mysite",
            "-c", "web", "--", "/bin/bash", "-c", "stdbuf -oL php -v",
        ]
        assert capsys.readouterr().out == "line1\nline2\n"

    def test_local_k8s_no_pod(self, monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_k8s_get_pod", lambda ns, svc: None)
        inst = {"host": "localhost", "orchestrator": "kubernetes"}
        assert _helpers._stream_in_container(
            "mysite", inst, "x", service="db") == 1
        assert ("no running pod found for service 'db'"
                in capsys.readouterr().err)
        assert _FakePopen.instances == []

    def test_remote_k8s_streams_over_ssh_without_retry(self):
        _FakePopen.returncodes = [255]
        inst = {"host": "remote1", "orchestrator": "kubernetes"}
        assert _helpers._stream_in_container(
            "mysite", inst, "php -v", retry_on_reset=True) == 255
        assert len(_FakePopen.instances) == 1
        argv = _FakePopen.instances[0].argv
        assert argv[:2] == ["ssh", "remote1"]
        assert ('exec kubectl exec -i "$pod" -n canasta-mysite -c web -- '
                "/bin/bash -c 'stdbuf -oL php -v'") in argv[2]


class TestGatherK8s:
    @pytest.mark.parametrize("dir_exists,running,status", [
        (False, "RUNNING", "NOT FOUND"),
        (True, "RUNNING", "RUNNING"),
        (True, "UNHEALTHY", "UNHEALTHY"),
        (True, "STOPPED", "STOPPED"),
    ])
    def test_status(self, monkeypatch, dir_exists, running, status):
        wikis = [{"id": "main", "url": "m.example.com"}]
        monkeypatch.setattr(_helpers, "_ssh_run",
                            lambda h, c: (0 if dir_exists else 1, ""))
        monkeypatch.setattr(_helpers, "_read_wikis", lambda p, h: wikis)
        monkeypatch.setattr(_helpers, "_web_state_k8s",
                            lambda i, h: running)
        monkeypatch.setattr(_helpers, "_read_restore_marker",
                            lambda p, h: None)
        detail = _helpers._gather_k8s("mysite", "/srv/mysite", "remote1")
        assert detail == {
            "id": "mysite", "host": "remote1", "path": "/srv/mysite",
            "orchestrator": "KUBERNETES", "status": status,
            "wikis": wikis if dir_exists else [],
        }

    def test_unreachable_host(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_ssh_run", lambda h, c: (255, ""))
        detail = _helpers._gather_k8s("mysite", "/srv/mysite", "remote1")
        assert detail["status"] == "UNREACHABLE"
        assert detail["wikis"] == []

    def test_interrupted_restore_flagged(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_check_dir_exists", lambda p, h: True)
        monkeypatch.setattr(_helpers, "_read_wikis", lambda p, h: [])
        monkeypatch.setattr(_helpers, "_web_state_k8s",
                            lambda i, h: "RUNNING")
        monkeypatch.setattr(_helpers, "_read_restore_marker",
                            lambda p, h: "2026-01-01")
        detail = _helpers._gather_k8s("mysite", "/srv/mysite", "localhost")
        assert detail["status"] == "RUNNING  [RESTORE INTERRUPTED]"


class TestGitopsStatusGitcryptLocked:
    def _lock(self, path, gpg):
        (path / "hosts").mkdir(parents=True)
        (path / "hosts" / "hosts.yaml").write_bytes(
            b"\x00GITCRYPT\x00\x8a\x01ciphertext")
        if gpg:
            keys = path / ".git-crypt" / "keys" / "default" / "0"
            keys.mkdir(parents=True)
            (keys / "ABCDEF.gpg").write_bytes(b"key")

    def test_k8s_skips_gitcrypt_check(self, monkeypatch, tmp_path):
        self._lock(tmp_path, gpg=True)
        inst = {"path": str(tmp_path), "host": "localhost",
                "orchestrator": "kubernetes"}
        monkeypatch.setattr(_helpers, "_resolve_instance",
                            lambda args: ("mysite", inst))
        monkeypatch.setattr(_helpers.subprocess, "run",
                            lambda argv, **kw: _Completed(0, ""))
        monkeypatch.setattr(gitops, "_gitops_argocd_status",
                            lambda i, h: None)
        monkeypatch.setattr(gitops, "_parse_gitops_status_k8s",
                            lambda out, i, a: "k8s status")
        assert gitops.cmd_gitops_status(
            _args(id="mysite", ssh_key=None)) == 0


class TestGitopsDiffRemote:
    def _inst(self, monkeypatch, orchestrator="compose"):
        inst = {"path": "/srv/mysite", "host": "remote1",
                "orchestrator": orchestrator}
        monkeypatch.setattr(_helpers, "_resolve_instance",
                            lambda args: ("mysite", inst))

    def test_k8s_falls_back(self, monkeypatch):
        self._inst(monkeypatch, orchestrator="kubernetes")
        assert gitops.cmd_gitops_diff(
            _args(id="mysite", stat=False, ssh_key=None)) \
            is direct_commands.FALLBACK
