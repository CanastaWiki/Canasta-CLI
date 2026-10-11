"""wiki check, maintenance wiki targeting, and the remote / K8s branches of
the direct-command helpers, gitops status/diff and rebuild."""

import os
import subprocess
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import direct_commands  # noqa: E402
from direct_commands import _helpers, gitops, maintenance, rebuild  # noqa: E402
from direct_commands import wiki_check  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_ssh(monkeypatch, tmp_path):
    # No hosts.yml and no extra ssh options, so ssh argv is predictable.
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("ANSIBLE_SSH_ARGS", "")
    monkeypatch.delenv("DOCKER_HOST", raising=False)


def _args(**kw):
    return types.SimpleNamespace(**kw)


class _SshRecorder:
    """Stand-in for _helpers._ssh_run that replays canned (rc, out) pairs."""

    def __init__(self, *responses):
        self.calls = []
        self.responses = list(responses)

    def __call__(self, host, cmd, docker_host=None, timeout=30):
        self.calls.append((host, cmd))
        if self.responses:
            return self.responses.pop(0)
        return 0, ""


# --- wiki check ---------------------------------------------------------


class TestWikiCheck:
    def _setup(self, monkeypatch, wikis, verdicts, host="localhost"):
        monkeypatch.setattr(
            _helpers, "_resolve_instance",
            lambda args: ("mysite", {"path": "/srv/mysite", "host": host}),
        )
        monkeypatch.setattr(_helpers, "_read_wikis", lambda path, h: wikis)
        probed = []

        def probe(url, h, instance_path=""):
            probed.append((url, h, instance_path))
            return verdicts[url]
        monkeypatch.setattr(_helpers, "_probe_wiki", probe)
        return probed

    @pytest.mark.parametrize("verdict,rc,expected", [
        (_helpers.WIKI_REACHABLE, 0,
         "Wiki 'main' is reachable at main.example.com."),
        (_helpers.WIKI_INDETERMINATE, 1,
         "Wiki 'main' could not be checked at main.example.com: "
         "host 'remote1' is unreachable."),
        (_helpers.WIKI_UNREACHABLE, 1,
         "Wiki 'main' could not be reached at main.example.com."),
    ])
    def test_probe_outcome(self, monkeypatch, capsys, verdict, rc, expected):
        probed = self._setup(
            monkeypatch, [{"id": "main", "url": " main.example.com "}],
            {"main.example.com": verdict}, host="remote1",
        )
        assert wiki_check.cmd_wiki_check(_args(id="mysite", host=None)) == rc
        out = capsys.readouterr().out
        assert out.startswith("Checking Canasta Wiki: mysite\n")
        assert expected in out
        assert probed == [("main.example.com", "remote1", "/srv/mysite")]

    def test_one_failure_fails_the_run_but_checks_all(
            self, monkeypatch, capsys):
        probed = self._setup(
            monkeypatch,
            [{"id": "a", "url": "a.example.com"},
             {"id": "b", "url": "b.example.com"}],
            {"a.example.com": _helpers.WIKI_UNREACHABLE,
             "b.example.com": _helpers.WIKI_REACHABLE},
        )
        assert wiki_check.cmd_wiki_check(_args(id="mysite", host=None)) == 1
        assert [p[0] for p in probed] == ["a.example.com", "b.example.com"]
        out = capsys.readouterr().out
        assert "Wiki 'b' is reachable" in out

    def test_host_flag_overrides_registry_host(self, monkeypatch):
        probed = self._setup(
            monkeypatch, [{"id": "main", "url": "m.example.com"}],
            {"m.example.com": _helpers.WIKI_REACHABLE}, host="remote1",
        )
        assert wiki_check.cmd_wiki_check(_args(id="mysite", host="h2")) == 0
        assert probed[0][1] == "h2"

    @pytest.mark.parametrize("wikis", [[], None])
    def test_no_wikis_is_an_error(self, monkeypatch, capsys, wikis):
        probed = self._setup(monkeypatch, wikis, {})
        assert wiki_check.cmd_wiki_check(_args(id="mysite", host=None)) == 1
        captured = capsys.readouterr()
        assert ("Error: no wikis configured for instance 'mysite'"
                in captured.err)
        assert "Checking" not in captured.out
        assert probed == []


# --- maintenance: wikis.yaml and per-wiki rc --------------------------------


class TestReadWikiIds:
    def _inst(self, tmp_path, content=None, host="localhost"):
        if content is not None:
            (tmp_path / "config").mkdir()
            (tmp_path / "config" / "wikis.yaml").write_text(content)
        return {"path": str(tmp_path), "host": host}

    def test_valid_file(self, tmp_path):
        inst = self._inst(tmp_path, (
            "wikis:\n"
            "- id: main\n  url: m.example.com\n"
            "- id: docs\n  url: m.example.com/docs\n"
        ))
        assert maintenance._read_wiki_ids(inst) == ["main", "docs"]

    def test_entries_without_id_are_skipped(self, tmp_path):
        inst = self._inst(tmp_path, (
            "wikis:\n"
            "- url: noid.example.com\n"
            "- id: ''\n"
            "- just-a-string\n"
            "- id: keep\n"
        ))
        assert maintenance._read_wiki_ids(inst) == ["keep"]

    def test_missing_file(self, tmp_path):
        assert maintenance._read_wiki_ids(self._inst(tmp_path)) == []

    def test_no_path(self):
        assert maintenance._read_wiki_ids({"host": "localhost"}) == []

    @pytest.mark.parametrize("content", [
        "wikis: [\n",          # unparseable
        "",                    # empty
        "wikis:\n",            # null list
        "other: 1\n",          # no wikis key
    ])
    def test_malformed_or_empty_file(self, tmp_path, content):
        assert maintenance._read_wiki_ids(
            self._inst(tmp_path, content)) == []

    def test_non_mapping_top_level(self, tmp_path):
        assert maintenance._read_wiki_ids(
            self._inst(tmp_path, "- id: main\n")) == []

    def test_remote_reads_over_ssh(self, monkeypatch):
        ssh = _SshRecorder((0, "wikis:\n- id: main\n- id: docs\n"))
        monkeypatch.setattr(_helpers, "_ssh_run", ssh)
        inst = {"path": "/srv/my site", "host": "remote1"}
        assert maintenance._read_wiki_ids(inst) == ["main", "docs"]
        assert ssh.calls == [
            ("remote1", "cat '/srv/my site/config/wikis.yaml' 2>/dev/null"),
        ]

    @pytest.mark.parametrize("response", [(1, ""), (0, ""), (255, "")])
    def test_remote_failure_or_empty(self, monkeypatch, response):
        monkeypatch.setattr(_helpers, "_ssh_run", _SshRecorder(response))
        inst = {"path": "/srv/mysite", "host": "remote1"}
        assert maintenance._read_wiki_ids(inst) == []

    def test_all_wikis_default_reads_the_real_file(
            self, tmp_path, monkeypatch, capsys):
        inst = self._inst(tmp_path, "wikis:\n- id: a\n- id: b\n")
        assert maintenance._resolve_wiki_targets(
            _args(wiki=None), inst) == ["a", "b"]

    def test_all_wikis_default_with_missing_file_errors(
            self, tmp_path, capsys):
        assert maintenance._resolve_wiki_targets(
            _args(wiki=None), self._inst(tmp_path)) == []
        assert "no wikis found in config/wikis.yaml" in capsys.readouterr().err


class TestMaintenanceUpdateRc:
    def _setup(self, monkeypatch, tmp_path, failing):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "wikis.yaml").write_text(
            "wikis:\n- id: a\n- id: b\n- id: c\n")
        inst = {"path": str(tmp_path), "host": "localhost"}
        monkeypatch.setattr(_helpers, "_resolve_instance",
                            lambda args: ("mysite", inst))
        streamed = []

        def stream(inst_id, i, command, service="web", retry_on_reset=False):
            streamed.append(command)
            return 7 if command in failing else 0
        monkeypatch.setattr(_helpers, "_stream_in_container", stream)
        monkeypatch.setattr(_helpers, "_exec_in_container",
                            lambda *a, **k: (0, "no"))
        return streamed

    def _cmd(self, script, wiki):
        return "php maintenance/%s --wiki='%s'" % (script, wiki)

    def test_update_failure_propagates_and_rest_still_run(
            self, monkeypatch, tmp_path, capsys):
        streamed = self._setup(monkeypatch, tmp_path,
                               {self._cmd("update.php", "a")})
        rc = maintenance.cmd_maintenance_update(
            _args(id="mysite", wiki=None, skip_jobs=False, skip_smw=False))
        assert rc == 7
        assert streamed == [
            self._cmd("update.php", "a"),
            self._cmd("update.php", "b"),
            self._cmd("update.php", "c"),
            self._cmd("runJobs.php", "a"),
            self._cmd("runJobs.php", "b"),
            self._cmd("runJobs.php", "c"),
        ]
        assert ("Maintenance update complete for: a, b, c"
                in capsys.readouterr().out)

    def test_runjobs_failure_propagates(self, monkeypatch, tmp_path):
        streamed = self._setup(monkeypatch, tmp_path,
                               {self._cmd("runJobs.php", "b")})
        rc = maintenance.cmd_maintenance_update(
            _args(id="mysite", wiki=None, skip_jobs=False, skip_smw=True))
        assert rc == 7
        assert self._cmd("runJobs.php", "c") in streamed

    def test_all_succeed(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, set())
        assert maintenance.cmd_maintenance_update(
            _args(id="mysite", wiki=None, skip_jobs=False,
                  skip_smw=True)) == 0


# --- _helpers remote / K8s branches -----------------------------------------


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestWriteRemoteOrLocalFile:
    def test_remote_pipes_content_through_ssh_cat(self, monkeypatch):
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            return _Completed(0)
        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)
        ok = _helpers._write_remote_or_local_file(
            "/srv/my site/.env", "remote1", "A=1\n")
        assert ok is True
        argv, kw = calls[0]
        assert argv == ["ssh", "remote1", "cat > '/srv/my site/.env'"]
        assert kw["input"] == "A=1\n"
        assert kw["text"] is True

    def test_remote_uses_hosts_yml_target(self, monkeypatch, tmp_path):
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        (cfg / "hosts.yml").write_text(
            "all:\n  hosts:\n    node1:\n"
            "      ansible_host: 10.0.0.5\n      ansible_user: ubuntu\n")
        calls = []
        monkeypatch.setattr(
            _helpers.subprocess, "run",
            lambda argv, **kw: calls.append(argv) or _Completed(0))
        assert _helpers._write_remote_or_local_file("/x", "node1", "c")
        assert calls[0][-2] == "ubuntu@10.0.0.5"

    def test_remote_nonzero_reports_stderr(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _helpers.subprocess, "run",
            lambda argv, **kw: _Completed(1, stderr="Permission denied\n"))
        assert _helpers._write_remote_or_local_file(
            "/srv/x", "remote1", "c") is False
        assert ("Error writing remote /srv/x: Permission denied"
                in capsys.readouterr().err)

    @pytest.mark.parametrize("exc", [
        subprocess.TimeoutExpired("ssh", 30), OSError("no ssh")])
    def test_remote_exception(self, monkeypatch, capsys, exc):
        def boom(argv, **kw):
            raise exc
        monkeypatch.setattr(_helpers.subprocess, "run", boom)
        assert _helpers._write_remote_or_local_file(
            "/srv/x", "remote1", "c") is False
        assert "Error writing remote /srv/x" in capsys.readouterr().err


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
            "-o", "jsonpath={.items[0].metadata.name}",
        ]

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
    def test_remote_compose(self, monkeypatch):
        ssh = _SshRecorder((0, "out"))
        monkeypatch.setattr(_helpers, "_ssh_run", ssh)
        inst = {"host": "remote1", "path": "/srv/my site",
                "composeCommand": "docker compose"}
        rc, out = _helpers._exec_in_container(
            "mysite", inst, "echo 'hi'", service="db")
        assert (rc, out) == (0, "out")
        assert ssh.calls == [(
            "remote1",
            "cd '/srv/my site' && docker compose exec -T db /bin/bash -c "
            "'echo '\\''hi'\\'''",
        )]

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


class _FakePopen:
    instances = []
    returncodes = []

    def __init__(self, argv, **kw):
        self.argv = argv
        self.kw = kw
        self._rc = (_FakePopen.returncodes.pop(0)
                    if _FakePopen.returncodes else 0)
        self.stdout = _Lines(["line1\n", "line2\n"])
        _FakePopen.instances.append(self)

    def wait(self, timeout=None):
        return self._rc


class _Lines:
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


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

    def test_remote_compose_command(self):
        inst = {"host": "remote1", "path": "/srv/mysite",
                "composeCommand": "docker compose",
                "dockerHost": "unix:///run/user/1000/docker.sock"}
        assert _helpers._stream_in_container("mysite", inst, "php -v") == 0
        assert _FakePopen.instances[0].argv == [
            "ssh", "remote1",
            "export DOCKER_HOST='unix:///run/user/1000/docker.sock'; "
            "cd '/srv/mysite' && docker compose exec -T web /bin/bash -c "
            "'stdbuf -oL php -v'",
        ]

    def test_remote_compose_retries_on_reset_when_asked(self, capsys):
        _FakePopen.returncodes = [255, 0]
        inst = {"host": "remote1", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        assert _helpers._stream_in_container(
            "mysite", inst, "x", retry_on_reset=True) == 0
        assert len(_FakePopen.instances) == 2

    def test_remote_compose_no_retry_by_default(self):
        _FakePopen.returncodes = [255, 0]
        inst = {"host": "remote1", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        assert _helpers._stream_in_container("mysite", inst, "x") == 255
        assert len(_FakePopen.instances) == 1

    def test_popen_oserror(self, monkeypatch, capsys):
        def boom(argv, **kw):
            raise OSError("no ssh")
        monkeypatch.setattr(_helpers.subprocess, "Popen", boom)
        inst = {"host": "remote1", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        assert _helpers._stream_in_container("mysite", inst, "x") == 1
        assert "Error: no ssh" in capsys.readouterr().err


class TestGatherK8s:
    @pytest.mark.parametrize("dir_exists,running,status", [
        (False, True, "NOT FOUND"),
        (True, True, "RUNNING"),
        (True, False, "STOPPED"),
    ])
    def test_status(self, monkeypatch, dir_exists, running, status):
        wikis = [{"id": "main", "url": "m.example.com"}]
        monkeypatch.setattr(_helpers, "_check_dir_exists",
                            lambda p, h: dir_exists)
        monkeypatch.setattr(_helpers, "_read_wikis", lambda p, h: wikis)
        monkeypatch.setattr(_helpers, "_check_running_k8s",
                            lambda i, h: running)
        monkeypatch.setattr(_helpers, "_read_restore_marker",
                            lambda p, h: None)
        detail = _helpers._gather_k8s("mysite", "/srv/mysite", "remote1")
        assert detail == {
            "id": "mysite", "host": "remote1", "path": "/srv/mysite",
            "orchestrator": "KUBERNETES", "status": status,
            "wikis": wikis if dir_exists else [],
        }

    def test_interrupted_restore_flagged(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_check_dir_exists", lambda p, h: True)
        monkeypatch.setattr(_helpers, "_read_wikis", lambda p, h: [])
        monkeypatch.setattr(_helpers, "_check_running_k8s",
                            lambda i, h: True)
        monkeypatch.setattr(_helpers, "_read_restore_marker",
                            lambda p, h: "2026-01-01")
        detail = _helpers._gather_k8s("mysite", "/srv/mysite", "localhost")
        assert detail["status"] == "RUNNING  [RESTORE INTERRUPTED]"


# --- gitops status / diff ---------------------------------------------------


class TestGitopsStatusGitcryptLocked:
    def _inst(self, monkeypatch, path, host="localhost"):
        inst = {"path": str(path), "host": host, "orchestrator": "compose"}
        monkeypatch.setattr(_helpers, "_resolve_instance",
                            lambda args: ("mysite", inst))

    def _lock(self, path, gpg):
        (path / "hosts").mkdir(parents=True)
        (path / "hosts" / "hosts.yaml").write_bytes(
            b"\x00GITCRYPT\x00\x8a\x01ciphertext")
        if gpg:
            keys = path / ".git-crypt" / "keys" / "default" / "0"
            keys.mkdir(parents=True)
            (keys / "ABCDEF.gpg").write_bytes(b"key")

    def test_local_gpg_mode(self, monkeypatch, tmp_path, capsys):
        self._lock(tmp_path, gpg=True)
        self._inst(monkeypatch, tmp_path)
        monkeypatch.setattr(_helpers.subprocess, "run", pytest.fail)
        rc = gitops.cmd_gitops_status(_args(id="mysite", ssh_key=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "git-crypt is locked in this checkout" in err
        assert ("Run 'git-crypt unlock' in %s (needs the GPG key" % tmp_path
                in err)

    def test_local_symmetric_mode(self, monkeypatch, tmp_path, capsys):
        self._lock(tmp_path, gpg=False)
        self._inst(monkeypatch, tmp_path)
        rc = gitops.cmd_gitops_status(_args(id="mysite", ssh_key=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "git-crypt is locked in this checkout" in err
        assert "Run 'git-crypt unlock <key>' in %s and retry." % tmp_path in err
        assert "GPG" not in err

    @pytest.mark.parametrize("mode_out,hint", [
        ("gpg\n", "needs the GPG key"),
        ("symmetric\n", "Run 'git-crypt unlock <key>' in /srv/mysite"),
    ])
    def test_remote_modes(self, monkeypatch, capsys, mode_out, hint):
        self._inst(monkeypatch, "/srv/mysite", host="remote1")
        ssh = _SshRecorder((0, "00474954435259505400\n"), (0, mode_out))
        monkeypatch.setattr(_helpers, "_ssh_run", ssh)
        assert gitops.cmd_gitops_status(
            _args(id="mysite", ssh_key=None)) == 1
        assert hint in capsys.readouterr().err
        assert len(ssh.calls) == 2
        assert "hosts/hosts.yaml" in ssh.calls[0][1]
        assert ".git-crypt/keys/default/0/*.gpg" in ssh.calls[1][1]

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

    def test_runs_script_over_ssh(self, monkeypatch, capsys):
        self._inst(monkeypatch)
        d = _helpers._SENTINEL + "\n"
        stdout = (
            "LocalSettings.php\n" + d + "patch-uc\n" + d + d + d
            + "config/x.yaml\n" + d + "patch-rc\n" + d + d + "FETCH:ok\n"
        )
        ssh = _SshRecorder((0, stdout))
        monkeypatch.setattr(_helpers, "_ssh_run", ssh)
        rc = gitops.cmd_gitops_diff(
            _args(id="mysite", stat=True, ssh_key=None))
        assert rc == 0
        host, script = ssh.calls[0]
        assert host == "remote1"
        assert script == gitops._gitops_diff_script(
            "/srv/mysite", stat=True, ssh_key=None)
        out = capsys.readouterr().out
        assert "Uncommitted changes: 1 file(s)" in out
        assert "Remote changes (would be applied on pull): 1 file(s)" in out
        assert "A restart would be needed after pulling." in out

    def test_connection_failure(self, monkeypatch, capsys):
        self._inst(monkeypatch)
        monkeypatch.setattr(_helpers, "_ssh_run", _SshRecorder((255, "")))
        assert gitops.cmd_gitops_diff(
            _args(id="mysite", stat=False, ssh_key=None)) == 1
        assert ("Error: failed to connect to remote1"
                in capsys.readouterr().err)

    def test_nonzero_with_output_still_reports(self, monkeypatch, capsys):
        self._inst(monkeypatch)
        monkeypatch.setattr(_helpers, "_ssh_run",
                            _SshRecorder((1, "FETCH:fail\n")))
        assert gitops.cmd_gitops_diff(
            _args(id="mysite", stat=False, ssh_key=None)) == 0

    def test_k8s_falls_back(self, monkeypatch):
        self._inst(monkeypatch, orchestrator="kubernetes")
        assert gitops.cmd_gitops_diff(
            _args(id="mysite", stat=False, ssh_key=None)) \
            is direct_commands.FALLBACK


# --- rebuild: remote compose config -----------------------------------------


class TestRebuildRemoteComposeConfig:
    def _patch(self, monkeypatch, *responses):
        monkeypatch.setattr(
            _helpers, "_compose_file_args",
            lambda path, host, devmode, sidecars:
            ["-f", "docker-compose.yml", "-f", "docker-compose.override.yml"])
        ssh = _SshRecorder(*responses)
        monkeypatch.setattr(_helpers, "_ssh_run", ssh)
        return ssh

    def test_docker_compose_json(self, monkeypatch):
        ssh = self._patch(monkeypatch, (0, (
            '{"services": {"web": {"build": {"context": "."}},'
            ' "db": {"image": "mariadb"}}}')))
        inst = {"host": "remote1", "path": "/srv/my site",
                "composeCommand": "docker compose"}
        assert rebuild._list_buildable_services(inst) == ["web"]
        assert ssh.calls == [(
            "remote1",
            "cd '/srv/my site' && docker compose -f docker-compose.yml "
            "-f docker-compose.override.yml config --format json",
        )]

    def test_podman_compose_yaml_with_profiles(self, monkeypatch):
        ssh = self._patch(monkeypatch, (0, (
            "services:\n  web:\n    build: .\n  varnish:\n    image: v\n")))
        monkeypatch.setattr(_helpers, "_read_env_for",
                            lambda inst, key: "internal-db,varnish")
        inst = {"host": "remote1", "path": "/srv/mysite",
                "composeCommand": "podman-compose"}
        assert rebuild._list_buildable_services(inst) == ["web"]
        assert ssh.calls[0][1] == (
            "cd '/srv/mysite' && podman-compose -f docker-compose.yml "
            "-f docker-compose.override.yml --profile internal-db "
            "--profile varnish config"
        )

    def test_remote_failure_returns_none(self, monkeypatch, capsys):
        self._patch(monkeypatch, (1, ""))
        inst = {"host": "remote1", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        assert rebuild._list_buildable_services(inst) is None
        assert ("Error: compose config failed on remote1"
                in capsys.readouterr().err)
