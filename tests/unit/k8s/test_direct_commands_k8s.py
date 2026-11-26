"""The direct commands' Kubernetes paths: the running check reads the web
deployment's replicas, locally or over ssh; version, status and gitops
status query the cluster with kubectl and Argo CD; exec and stream look up
the web pod and run kubectl exec, on the instance's host when it is remote;
start, stop, gitops diff and backup list hand Kubernetes instances to
Ansible; the argocd commands reach Argo CD's resources; and scale validates
its request before handing it to Ansible."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from test_direct_commands import direct_commands


class TestCheckRunningK8s:
    """canasta list's K8s status check must SSH to the instance's host
    (where the kubeconfig pointing at the cluster lives) instead of
    running kubectl locally against whatever the laptop's kubeconfig
    happens to be (Docker Desktop in the worst case)."""

    def test_local_running(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "1"})(),
        )
        assert direct_commands._check_running_k8s("mywiki", "localhost")

    def test_local_zero_replicas_is_not_running(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "0"})(),
        )
        assert not direct_commands._check_running_k8s("mywiki", "localhost")

    def test_remote_uses_ssh(self, monkeypatch):
        """Remote hosts must dispatch via _ssh_run, not subprocess.run.
        Guards against the original bug where a remote K8s instance
        was reported STOPPED because the local kubectl had no idea
        about the remote cluster."""
        called = {}

        def fake_ssh(host, cmd):
            called["host"] = host
            called["cmd"] = cmd
            return 0, "1\n"

        def fail_subprocess(*args, **kwargs):
            raise AssertionError(
                "remote check must not invoke subprocess.run directly"
            )

        monkeypatch.setattr(direct_commands._helpers, "_ssh_run", fake_ssh)
        monkeypatch.setattr(subprocess, "run", fail_subprocess)
        assert direct_commands._check_running_k8s("mywiki", "node1")
        assert called["host"] == "node1"
        assert "kubectl" in called["cmd"]
        assert "canasta-mywiki-web" in called["cmd"]
        assert "canasta-mywiki" in called["cmd"]  # namespace

    def test_remote_not_running(self, monkeypatch):
        monkeypatch.setattr(direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (0, ""),
        )
        assert not direct_commands._check_running_k8s("mywiki", "node1")

    def test_remote_ssh_failure_treated_as_stopped(self, monkeypatch):
        monkeypatch.setattr(direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (255, ""),  # SSH could not connect
        )
        assert not direct_commands._check_running_k8s("mywiki", "node1")


class TestCmdVersionInstanceModes:
    def _setup(self, tmp_path, monkeypatch, instances):
        """Build a script_dir + registry under tmp_path and wire
        direct_commands at it. Returns (script_dir, config_dir)."""
        script_dir = tmp_path / "script"
        script_dir.mkdir()
        (script_dir / "VERSION").write_text("4.0.0\n")
        (script_dir / "CANASTA_VERSION").write_text("3.6.3\n")
        (script_dir / "BUILD_COMMIT").write_text("abc1234\n")
        (script_dir / "BUILD_DATE").write_text("2026-04-23 00:00:00\n")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "conf.json").write_text(json.dumps({"Instances": instances}))
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        monkeypatch.setattr(direct_commands._helpers, "_get_script_dir", lambda: str(script_dir))
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(config_dir))
        return script_dir, config_dir

    def test_k8s_instance_probes_via_kubectl(
        self, tmp_path, monkeypatch, capsys
    ):
        """A Kubernetes instance must probe the running version by
        exec'ing the web pod via kubectl, not `docker compose` (which
        finds no stack and would always report "(not running)")."""
        site = tmp_path / "site"
        site.mkdir()
        (site / ".env").write_text("CANASTA_IMAGE=canasta:3.6.3\n")
        self._setup(tmp_path, monkeypatch, {
            "site": {"path": str(site), "host": "localhost",
                     "orchestrator": "kubernetes"},
        })
        monkeypatch.chdir(site)
        captured = {}

        def fake_run(*a, **kw):
            captured["cmd"] = a[0][2] if a and len(a[0]) > 2 else ""
            return type("R", (), {
                "returncode": 0, "stdout": "3.6.3-running\n",
            })()

        monkeypatch.setattr(subprocess, "run", fake_run)
        args = type("Args", (), {"id": None, "cli_only": False, "host": None})()
        rc = direct_commands.cmd_version(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Instance 'site': canasta:3.6.3 (running: 3.6.3-running)" in out
        # Probe must target the k8s web pod, not a Compose stack.
        assert "kubectl" in captured["cmd"]
        assert "canasta-site" in captured["cmd"]
        assert "docker compose" not in captured["cmd"]


class TestLifecycleCommands:
    def test_k8s_start_falls_back(self, monkeypatch):
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("k8s-site", {
                "path": "/srv/k8s-site",
                "orchestrator": "kubernetes",
            }),
        )
        args = type("Args", (), {"id": "k8s-site"})()
        assert direct_commands.cmd_start(args) is direct_commands.FALLBACK

    def test_k8s_stop_falls_back(self, monkeypatch):
        # K8s stop must run kubectl on the instance's host (where the
        # cluster kubeconfig lives), so it defers to Ansible — which
        # resolves the host and switches the connection — rather than
        # running kubectl on the controller.
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("k8s-site", {
                "path": "/srv/k8s-site",
                "orchestrator": "kubernetes",
            }),
        )
        args = type("Args", (), {"id": "k8s-site"})()
        assert direct_commands.cmd_stop(args) is direct_commands.FALLBACK


class TestGitopsArgocdStatus:
    def test_kubectl_missing_returns_not_registered(self, monkeypatch):
        def fake_run(*a, **kw):
            raise OSError("kubectl not found")
        monkeypatch.setattr(subprocess, "run", fake_run)
        result = direct_commands._gitops_argocd_status("mysite")
        assert result == ("Not registered", "N/A", "never", "unknown", None)

    def test_no_application_returns_not_registered(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 1, "stdout": "",
                "stderr": "not found",
            })(),
        )
        result = direct_commands._gitops_argocd_status("mysite")
        assert result == ("Not registered", "N/A", "never", "unknown", None)

    def test_parses_synced_application(self, monkeypatch):
        app = {
            "status": {
                "sync": {"status": "Synced", "revision": "abcdef1234567890"},
                "health": {"status": "Healthy"},
                "operationState": {"finishedAt": "2026-04-23T10:00:00Z"},
            }
        }
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": json.dumps(app),
            })(),
        )
        sync, health, last, rev, hint = direct_commands._gitops_argocd_status("mysite")
        assert sync == "Synced"
        assert health == "Healthy"
        assert last == "2026-04-23T10:00:00Z"
        assert rev == "abcdef1"  # truncated to 7 chars
        assert hint is None

    def test_malformed_json_returns_unknown(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "not json",
            })(),
        )
        result = direct_commands._gitops_argocd_status("mysite")
        assert result == ("Unknown", "Unknown", "never", "unknown", None)

    def test_remote_host_queries_over_ssh(self, monkeypatch):
        app = {
            "status": {
                "sync": {"status": "Synced", "revision": "abcdef1234567890"},
                "health": {"status": "Healthy"},
                "operationState": {"finishedAt": "2026-04-23T10:00:00Z"},
            }
        }
        calls = {}

        def fake_ssh(host, cmd):
            calls["host"] = host
            calls["cmd"] = cmd
            return (0, json.dumps(app))

        def fail_run(*a, **kw):
            raise AssertionError("kubectl must not run locally for remote host")

        monkeypatch.setattr(direct_commands._helpers, "_ssh_run", fake_ssh)
        monkeypatch.setattr(subprocess, "run", fail_run)
        sync, health, last, rev, _ = direct_commands._gitops_argocd_status(
            "mysite", "admin@remote",
        )
        assert sync == "Synced"
        assert health == "Healthy"
        assert calls["host"] == "admin@remote"
        assert "kubectl get application canasta-mysite" in calls["cmd"]

    def test_localhost_queries_locally(self, monkeypatch):
        app = {"status": {"sync": {"status": "Synced", "revision": "abc"},
                          "health": {"status": "Healthy"}}}

        def fake_run(*a, **kw):
            return type("R", (), {"returncode": 0, "stdout": json.dumps(app)})()

        def fail_ssh(*a, **kw):
            raise AssertionError("localhost must not use ssh")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(direct_commands._helpers, "_ssh_run", fail_ssh)
        sync, _, _, _, _ = direct_commands._gitops_argocd_status("mysite", "localhost")
        assert sync == "Synced"

    def test_sops_decrypt_error_returns_hint(self, monkeypatch):
        app = {
            "status": {
                "sync": {"status": "Unknown", "revision": "abc"},
                "health": {"status": "Unknown"},
                "conditions": [
                    {"type": "ComparisonError",
                     "message": "Failed to load target state: failed to "
                                "generate manifest: sops: no identity matched "
                                "any of the recipients"},
                ],
            }
        }
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": json.dumps(app),
            })(),
        )
        sync, _, _, _, hint = direct_commands._gitops_argocd_status("mysite")
        assert sync == "Unknown"
        assert hint is not None
        assert "sops-age" in hint

    def test_non_sops_error_returns_no_hint(self, monkeypatch):
        app = {
            "status": {
                "sync": {"status": "OutOfSync", "revision": "abc"},
                "health": {"status": "Degraded"},
                "conditions": [
                    {"type": "ComparisonError",
                     "message": "Failed to load target state: connection "
                                "refused"},
                ],
            }
        }
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": json.dumps(app),
            })(),
        )
        _, _, _, _, hint = direct_commands._gitops_argocd_status("mysite")
        assert hint is None


class TestParseGitopsStatusK8s:
    def _make_output(self, hostname="myhost", commit="abc1234", revcount="0\t0",
                     fetch="ok", staged="", unstaged="", wikis=None,
                     template=None, untracked=None):
        d = direct_commands._SENTINEL
        # Parts: hostname (0), hosts.yaml (1), commit (2), applied (3),
        # staged (4), unstaged (5), fetch-status + rev-list (6), then optionally
        # wikis (7), template (8), git-status porcelain (9).
        remote = "FETCH:%s\n%s" % (fetch, revcount)
        out = (
            hostname + "\n" + d + "\n"
            + "\n" + d + "\n"
            + commit + "\n" + d + "\n"
            + "\n" + d + "\n"
            + staged + "\n" + d + "\n"
            + unstaged + "\n" + d + "\n"
            + remote + "\n"
        )
        if wikis is not None or template is not None or untracked is not None:
            out += (
                d + "\n" + (wikis or "") + "\n"
                + d + "\n" + (template or "") + "\n"
                + d + "\n" + (untracked or "")
            )
        return out

    def test_synced_healthy(self):
        out = self._make_output()
        argocd = ("Synced", "Healthy", "2026-04-23T10:00:00Z", "abcdef1")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Host:             myhost" in result
        assert "Canasta ID:       mysite" in result
        assert "Current commit:   abc1234" in result
        assert "Ahead of remote:  0" in result
        assert "Sync status:    Synced" in result
        assert "Health status:  Healthy" in result
        assert "Applied rev:    abcdef1" in result
        assert "OutOfSync" not in result  # no remediation note

    def test_out_of_sync_adds_note(self):
        out = self._make_output()
        argocd = ("OutOfSync", "Healthy", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Sync status:    OutOfSync" in result
        assert "canasta gitops sync" in result

    def test_git_without_commit_reported_as_unfinished_setup(self):
        out = self._make_output(hostname="MISSING", commit="NOCOMMIT")
        argocd = ("Unknown", "Unknown", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Canasta ID:       mysite" in result
        assert "setup did not finish" in result
        assert "canasta gitops init --reinit" in result
        assert "NOCOMMIT" not in result

    def test_missing_host_file_shows_unknown(self):
        out = self._make_output(hostname="MISSING")
        argocd = ("Not registered", "N/A", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Host:             unknown" in result
        assert "Sync status:    Not registered" in result

    def test_ahead_behind_parsed(self):
        out = self._make_output(revcount="3\t2")
        argocd = ("Synced", "Healthy", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Ahead of remote:  3" in result
        assert "Behind remote:    2" in result

    def test_fetch_failed_shows_unknown(self):
        # A failed fetch must not print "Ahead/Behind: 0" as if in sync.
        out = self._make_output(fetch="fail", revcount="0\t0")
        argocd = ("Synced", "Healthy", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Remote status:    unknown" in result
        assert "Ahead of remote:" not in result
        assert "--ssh-key" in result

    def test_no_upstream_has_no_ssh_key_hint(self):
        out = self._make_output(fetch="ok", revcount="REVLIST:fail")
        argocd = ("Synced", "Healthy", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "no upstream tracking" in result
        assert "--ssh-key" not in result

    def test_untracked_files_surfaced(self):
        # A K8s instance with an uncaptured file (e.g. wikis.yaml.template
        # backfilled by an upgrade) must flag it, not just report Ahead: 0.
        out = self._make_output(untracked="?? wikis.yaml.template")
        argocd = ("Synced", "Healthy", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Untracked files (1):" in result
        assert "wikis.yaml.template" in result
        # Must point at the reconcile command, not a plain add of the template.
        assert "canasta gitops add config/wikis.yaml" in result
        assert "gitops add <file>" not in result
        # Advisory must not shadow the Argo section.
        assert "Sync status:    Synced" in result

    def test_uncaptured_wikis_edit_surfaced(self):
        live = "wikis:\n- id: main\n  url: rs.example.com\n  name: New Name\n"
        tmpl = "wikis:\n  - id: main\n    url: {{wiki_url_main}}\n    name: \"main\"\n"
        out = self._make_output(wikis=live, template=tmpl)
        argocd = ("Synced", "Healthy", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Uncaptured config/wikis.yaml edits" in result

    def test_clean_tree_shows_no_advisory(self):
        # No working-tree changes: the Argo section renders as before, with
        # none of the advisory headers.
        out = self._make_output(untracked="")
        argocd = ("Synced", "Healthy", "never", "unknown")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Untracked files" not in result
        assert "Uncaptured config/wikis.yaml edits" not in result
        assert "Argo CD:" in result

    def test_sops_hint_surfaced(self):
        out = self._make_output()
        argocd = ("Unknown", "Unknown", "never", "unknown",
                  "Argo CD reported a SOPS/age decryption error. Check the "
                  "sops-age Secret.")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "SOPS/age decryption error" in result
        assert "sops-age" in result

    def test_four_tuple_argocd_still_supported(self):
        # Back-compat: a 4-tuple (no hint slot) must not raise.
        out = self._make_output()
        argocd = ("Synced", "Healthy", "never", "abcdef1")
        result = direct_commands._parse_gitops_status_k8s(out, "mysite", argocd)
        assert "Sync status:    Synced" in result


class TestCmdGitopsStatusK8s:
    """cmd_gitops_status must branch on orchestrator. For K8s instances,
    it should combine git state (via SSH) with Argo CD state (via kubectl)."""

    def test_k8s_instance_queries_argocd(self, monkeypatch, capsys):
        d = direct_commands._SENTINEL
        ssh_output = (
            "k8shost\n" + d + "\n"
            + "\n" + d + "\n"
            + "def5678\n" + d + "\n"
            + "\n" + d + "\n"
            + "\n" + d + "\n"
            + "\n" + d + "\n"
            + "0\t0\n"
        )
        app = {
            "status": {
                "sync": {"status": "Synced", "revision": "def5678aaaaaaaaaa"},
                "health": {"status": "Healthy"},
                "operationState": {"finishedAt": "2026-04-23T12:00:00Z"},
            }
        }
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("mysite", {
                "path": "/srv/mysite",
                "orchestrator": "kubernetes",
                "host": "admin@k8s-control",
            }),
        )
        # Remote host: both the git status and the Argo CD kubectl run
        # over SSH against the host's kubeconfig, not the laptop's.

        def fake_ssh(host, cmd):
            if "kubectl get application" in cmd:
                return (0, json.dumps(app))
            return (0, ssh_output)
        monkeypatch.setattr(direct_commands._helpers, "_ssh_run", fake_ssh)

        def fail_run(*a, **kw):
            raise AssertionError("kubectl must not run locally for remote host")
        monkeypatch.setattr(subprocess, "run", fail_run)
        args = type("Args", (), {"id": "mysite"})()
        rc = direct_commands.cmd_gitops_status(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Host:             k8shost" in out
        assert "Current commit:   def5678" in out
        assert "Sync status:    Synced" in out
        assert "Health status:  Healthy" in out
        # K8s output must NOT include the compose-only lines
        assert "Role:" not in out
        assert "Pull requests:" not in out
        assert "No changes." not in out


class TestExecInContainer:
    def test_k8s(self, monkeypatch):
        monkeypatch.setattr(direct_commands._helpers, "_k8s_get_pod",
            lambda ns, svc: "canasta-test-web-abc123",
        )
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "Cite\n",
            })(),
        )
        rc, out = direct_commands._exec_in_container(
            "test",
            {"path": "/srv/test", "orchestrator": "kubernetes"},
            "find extensions",
        )
        assert rc == 0

    def test_k8s_no_pod(self, monkeypatch):
        monkeypatch.setattr(direct_commands._helpers, "_k8s_get_pod",
            lambda ns, svc: None,
        )
        rc, out = direct_commands._exec_in_container(
            "test",
            {"path": "/srv/test", "orchestrator": "kubernetes"},
            "find extensions",
        )
        assert rc == 1


class TestStreamInContainerStdin:
    @staticmethod
    def _fake_popen(captured):
        def fake(argv, **kw):
            captured["argv"] = argv
            captured["kwargs"] = kw
            return type("P", (), {
                "stdout": __import__("io").StringIO(""),
                "wait": lambda self: 0,
            })()
        return fake

    def test_k8s_exec_forwards_stdin(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(direct_commands._helpers, "_k8s_get_pod",
            lambda ns, svc: "canasta-test-web-abc123",
        )
        monkeypatch.setattr(subprocess, "Popen", self._fake_popen(captured))
        rc = direct_commands._helpers._stream_in_container(
            "test", {"orchestrator": "kubernetes"}, "php maintenance/run.php eval",
        )
        assert rc == 0
        assert captured["argv"][:3] == ["kubectl", "exec", "-i"]
        # stdin must not be overridden away from inheritance.
        assert "stdin" not in captured["kwargs"]


class TestCmdGitopsDiff:
    def test_k8s_falls_back(self, monkeypatch):
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("k8s", {"path": "/p", "orchestrator": "kubernetes"}),
        )
        args = type("Args", (), {"id": "k8s"})()
        assert direct_commands.cmd_gitops_diff(args) is direct_commands.FALLBACK


class TestBackupList:
    def test_k8s_falls_back(self, monkeypatch):
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("k8s", {"path": "/p", "orchestrator": "kubernetes"}),
        )
        args = type("Args", (), {"id": "k8s"})()
        assert direct_commands.cmd_backup_list(args) is direct_commands.FALLBACK


class TestCanastaStatus:
    def _args(self, **kw):
        from argparse import Namespace
        defaults = {"id": None, "verbose": False}
        defaults.update(kw)
        return Namespace(**defaults)

    def test_k8s_routes_through_ssh(self, monkeypatch, capsys):
        monkeypatch.setattr(direct_commands._helpers, "_read_registry",
            lambda p: {
                "mysite": {
                    "id": "mysite", "orchestrator": "kubernetes",
                    "host": "node1", "path": "/home/admin/canasta-instances/mysite",
                },
            },
        )
        monkeypatch.setattr(direct_commands._helpers, "_check_running_k8s",
            lambda inst, host: True,
        )
        ssh_calls = []

        def fake_ssh(host, cmd):
            ssh_calls.append((host, cmd))
            return 0, "stub-output"

        monkeypatch.setattr(direct_commands._helpers, "_ssh_run", fake_ssh)
        # Make sure no local subprocess is called for K8s sections.
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("K8s sections must dispatch via SSH")
            ),
        )

        rc = direct_commands.cmd_status(self._args(id="mysite"))
        assert rc == 0
        # Header sanity
        out = capsys.readouterr().out
        assert "Instance:     mysite" in out
        assert "Host:         node1" in out
        assert "KUBERNETES" in out
        assert "RUNNING" in out
        # Sections requested
        cmds = [c for _, c in ssh_calls]
        joined = " | ".join(cmds)
        for piece in [
            "kubectl get pods -o wide -n canasta-mysite",
            "kubectl get pvc -n canasta-mysite",
            "kubectl get ingress -n canasta-mysite",
            "kubectl get certificate -n canasta-mysite",
        ]:
            assert piece in joined


class TestArgocdCommands:
    """canasta argocd password / apps / ui — direct_only commands that
    SSH to the target host (or run locally) and proxy through to Argo
    CD's K8s resources. They replace ssh+sudo-k3s-kubectl reach in
    docs."""

    def _args(self, **kw):
        from argparse import Namespace
        defaults = {"host": None, "port": None, "verbose": False}
        defaults.update(kw)
        return Namespace(**defaults)

    def test_password_remote_ssh(self, monkeypatch, capsys):
        monkeypatch.setattr(direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (0, "secret123\n"),
        )
        rc = direct_commands.cmd_argocd_password(self._args(host="node1"))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "secret123"

    def test_password_secret_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (1, ""),
        )
        rc = direct_commands.cmd_argocd_password(self._args(host="node1"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "initial-admin secret not found" in err

    def test_apps_remote(self, monkeypatch, capsys):
        sample = (
            "NAME              SYNC STATUS   HEALTH STATUS\n"
            "canasta-mysite    Synced        Healthy\n"
        )
        monkeypatch.setattr(direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (0, sample),
        )
        rc = direct_commands.cmd_argocd_apps(self._args(host="node1"))
        assert rc == 0
        assert "canasta-mysite" in capsys.readouterr().out

    def test_ui_remote_invokes_ssh_tunnel(self, monkeypatch):
        """`canasta argocd ui --host node1` must run `ssh -L … kubectl
        port-forward …`, not a local kubectl call."""
        # Suppress the up-front password fetch.
        monkeypatch.setattr(direct_commands.argocd, "_argocd_admin_password",
            lambda host: (0, "shh"),
        )
        # Stub host resolution to a fixed user@host string.
        monkeypatch.setattr(direct_commands._helpers, "_resolve_ssh_target",
            lambda host: "admin@node1.example",
        )
        captured = {}

        def fake_call(cmd):
            captured["cmd"] = cmd
            return 0

        monkeypatch.setattr(subprocess, "call", fake_call)
        rc = direct_commands.cmd_argocd_ui(
            self._args(host="node1", port=9443)
        )
        assert rc == 0
        cmd = captured["cmd"]
        assert cmd[0] == "ssh"
        assert "-L" in cmd
        # Tunnel: <port>:localhost:<port>
        assert "9443:localhost:9443" in cmd
        # Target host is the resolved SSH target, not the canasta name.
        assert "admin@node1.example" in cmd
        # The remote command runs kubectl port-forward.
        joined = " ".join(cmd)
        assert "kubectl port-forward" in joined
        assert "argocd-server" in joined
        assert "9443:443" in joined

    def test_ui_local_uses_kubectl_directly(self, monkeypatch):
        monkeypatch.setattr(direct_commands.argocd, "_argocd_admin_password",
            lambda host: (0, ""),  # secret missing — should still proceed
        )
        captured = {}

        def fake_call(cmd):
            captured["cmd"] = cmd
            return 0

        monkeypatch.setattr(subprocess, "call", fake_call)
        rc = direct_commands.cmd_argocd_ui(self._args(host=None, port=8443))
        assert rc == 0
        cmd = captured["cmd"]
        # Local mode: no ssh, just sh -c "kubectl port-forward ..."
        assert cmd[0] != "ssh"
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        assert "kubectl port-forward" in joined
        assert "8443:443" in joined


class TestScale:
    def _args(self, **kw):
        from argparse import Namespace
        defaults = {"id": "mysite", "replicas": 3, "component": "web"}
        defaults.update(kw)
        return Namespace(**defaults)

    def _k8s_inst(self, **kw):
        return {
            "id": "mysite",
            "path": "/srv/mysite",
            "host": "localhost",
            "orchestrator": "kubernetes",
            **kw,
        }

    def test_registered(self):
        assert direct_commands.is_direct_command("scale")

    def test_rejects_unsupported_component(self, monkeypatch, capsys):
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("mysite", self._k8s_inst()),
        )
        rc = direct_commands.cmd_scale(self._args(component="varnish"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "varnish" in err
        assert "only 'web'" in err

    def test_rejects_zero_replicas(self, monkeypatch, capsys):
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("mysite", self._k8s_inst()),
        )
        rc = direct_commands.cmd_scale(self._args(replicas=0))
        assert rc == 1
        assert "≥ 1" in capsys.readouterr().err

    def test_rejects_non_integer_replicas(self, monkeypatch, capsys):
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("mysite", self._k8s_inst()),
        )
        rc = direct_commands.cmd_scale(self._args(replicas="abc"))
        assert rc == 1
        assert "must be an integer" in capsys.readouterr().err

    def test_valid_k8s_request_hands_off_to_ansible(self, monkeypatch):
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("mysite", self._k8s_inst()),
        )

        def no_subprocess(*a, **kw):
            raise AssertionError("scale must not run helm itself")

        monkeypatch.setattr(subprocess, "call", no_subprocess)
        rc = direct_commands.cmd_scale(self._args(replicas=2))
        assert rc is direct_commands.FALLBACK


class TestRemoteK8sStreamAndExec:
    """maintenance script/extension/update stream through
    _stream_in_container, and lighter helpers use _exec_in_container; for a
    remote Kubernetes instance both must run the pod lookup and the exec on
    the instance's host via ssh — kubectl on the controller
    has no kubeconfig for the cluster."""

    INST = {"id": "rsdev", "orchestrator": "k8s",
            "host": "op@cp.example.com", "path": "/tmp/i"}

    def test_stream_remote_goes_via_ssh(self, monkeypatch):
        captured = {}

        def fake_popen_run(argv, cwd):
            captured["argv"] = argv
            return 0

        monkeypatch.setattr(
            direct_commands._helpers, "_k8s_get_pod",
            lambda *a, **k: pytest.fail(
                "local pod lookup must not run for a remote instance"))
        monkeypatch.setattr(direct_commands._helpers, "_stream_argv", fake_popen_run,
                            raising=False)
        # _stream_in_container drives the argv through an inner _run();
        # patch subprocess.Popen to capture without spawning.

        class FakeProc:
            returncode = 0

            def __init__(self):
                import io
                self.stdout = io.StringIO("")

            def wait(self):
                return 0

        def fake_popen(argv, **kw):
            captured["argv"] = argv
            return FakeProc()

        monkeypatch.setattr(direct_commands._helpers.subprocess, "Popen", fake_popen)
        rc = direct_commands._helpers._stream_in_container("rsdev", self.INST, "php x.php")
        assert rc == 0
        assert captured["argv"][0] == "ssh"
        assert "op@cp.example.com" in captured["argv"]
        remote = captured["argv"][-1]
        assert "kubectl get pods" in remote
        assert "app.kubernetes.io/component=web" in remote
        assert "--field-selector=status.phase=Running" in remote
        assert "kubectl exec -i" in remote
        assert "canasta-rsdev" in remote
        assert "stdbuf -oL" in remote

    def test_exec_remote_goes_via_ssh_run(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            direct_commands._helpers, "_k8s_get_pod",
            lambda *a, **k: pytest.fail(
                "local pod lookup must not run for a remote instance"))

        def fake_ssh_run(host, cmd, timeout=30):
            captured["host"] = host
            captured["cmd"] = cmd
            return 0, "ok"

        monkeypatch.setattr(direct_commands._helpers, "_ssh_run", fake_ssh_run)
        rc, out = direct_commands._helpers._exec_in_container("rsdev", self.INST, "true")
        assert (rc, out) == (0, "ok")
        assert captured["host"] == "op@cp.example.com"
        assert "kubectl get pods" in captured["cmd"]
        assert "no running pod found for service 'web'" in captured["cmd"]

    def test_local_k8s_path_unchanged(self, monkeypatch):
        inst = dict(self.INST, host="localhost")
        monkeypatch.setattr(direct_commands._helpers, "_k8s_get_pod", lambda *a, **k: "pod-1")

        def fake_run(argv, **kw):
            fake_run.argv = argv
            return SimpleNamespace(returncode=0, stdout="out")

        monkeypatch.setattr(direct_commands._helpers.subprocess, "run", fake_run)
        rc, out = direct_commands._helpers._exec_in_container("rsdev", inst, "true")
        assert rc == 0
        assert fake_run.argv[0] == "kubectl"
