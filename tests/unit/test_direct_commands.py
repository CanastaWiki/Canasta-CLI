"""Tests for direct_commands.py — Ansible-bypassing command implementations."""

import json
import os
import subprocess
import sys

import pytest

# Ensure direct_commands is importable from the repo root.
REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Create a conf.json with local instances + set CANASTA_CONFIG_DIR."""
    site_a = tmp_path / "siteA"
    site_a.mkdir()
    config_a = site_a / "config"
    config_a.mkdir()
    (config_a / "wikis.yaml").write_text(
        "wikis:\n"
        "  - id: main\n"
        "    url: example.com\n"
        "  - id: docs\n"
        "    url: example.com/docs\n"
    )

    site_b = tmp_path / "siteB"
    site_b.mkdir()
    config_b = site_b / "config"
    config_b.mkdir()
    (config_b / "wikis.yaml").write_text(
        "wikis:\n"
        "  - id: wiki\n"
        "    url: other.org\n"
    )

    data = {
        "Instances": {
            "siteA": {
                "id": "siteA",
                "path": str(site_a),
                "orchestrator": "compose",
            },
            "siteB": {
                "id": "siteB",
                "path": str(site_b),
                "orchestrator": "compose",
            },
        }
    }
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps(data, indent=4))
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
    return tmp_path, data


@pytest.fixture
def registry_with_remote(tmp_path, monkeypatch):
    """Registry with a local and a remote instance."""
    local = tmp_path / "local"
    local.mkdir()
    config_l = local / "config"
    config_l.mkdir()
    (config_l / "wikis.yaml").write_text(
        "wikis:\n  - id: main\n    url: local.test\n"
    )

    data = {
        "Instances": {
            "local": {
                "id": "local",
                "path": str(local),
                "orchestrator": "compose",
            },
            "remote": {
                "id": "remote",
                "path": "/srv/canasta/remote",
                "orchestrator": "compose",
                "host": "admin@prod.example.com",
            },
        }
    }
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps(data, indent=4))
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
    return tmp_path, data


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_list_is_registered(self):
        assert direct_commands.is_direct_command("list")

    def test_unknown_command_not_registered(self):
        assert not direct_commands.is_direct_command("create")

    def test_run_calls_handler(self, monkeypatch):
        called = {}
        monkeypatch.setitem(
            direct_commands.DIRECT_COMMANDS,
            "_test_cmd",
            lambda args: called.update(ran=True) or 0,
        )
        rc = direct_commands.run_direct_command("_test_cmd", None)
        assert rc == 0
        assert called.get("ran")


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestHostMatches:
    def test_exact_match(self):
        assert direct_commands._host_matches("prod.example.com", "prod.example.com")

    def test_user_at_host(self):
        assert direct_commands._host_matches("admin@prod.example.com", "prod.example.com")

    def test_full_match_with_user(self):
        assert direct_commands._host_matches("admin@prod.example.com", "admin@prod.example.com")

    def test_no_match(self):
        assert not direct_commands._host_matches("staging.example.com", "prod.example.com")


class TestFilterByHost:
    def test_no_filter_returns_all(self, registry):
        _, data = registry
        result = direct_commands._filter_by_host(data["Instances"], None)
        assert len(result) == 2

    def test_filter_localhost(self, registry):
        _, data = registry
        result = direct_commands._filter_by_host(data["Instances"], "localhost")
        assert len(result) == 2

    def test_filter_remote(self, registry_with_remote):
        _, data = registry_with_remote
        result = direct_commands._filter_by_host(
            data["Instances"], "prod.example.com",
        )
        assert list(result.keys()) == ["remote"]


class TestReadRegistry:
    def test_reads_instances(self, registry):
        tmp_path, _ = registry
        instances = direct_commands._read_registry(str(tmp_path / "conf.json"))
        assert "siteA" in instances
        assert "siteB" in instances

    def test_missing_file_returns_empty(self, tmp_path):
        instances = direct_commands._read_registry(str(tmp_path / "nope.json"))
        assert instances == {}

    def test_legacy_installations_key(self, tmp_path):
        data = {"Installations": {"old": {"path": "/old"}}}
        conf = tmp_path / "conf.json"
        conf.write_text(json.dumps(data))
        instances = direct_commands._read_registry(str(conf))
        assert "old" in instances


class TestReadWikis:
    def test_reads_local_wikis(self, registry):
        tmp_path, data = registry
        path = data["Instances"]["siteA"]["path"]
        wikis = direct_commands._read_wikis(path, "localhost")
        assert len(wikis) == 2
        assert wikis[0]["id"] == "main"
        assert wikis[1]["id"] == "docs"

    def test_missing_file_returns_empty(self, tmp_path):
        wikis = direct_commands._read_wikis(str(tmp_path / "nodir"), "localhost")
        assert wikis == []


class TestShellQuote:
    def test_simple(self):
        assert direct_commands._shell_quote("hello") == "'hello'"

    def test_with_single_quotes(self):
        assert direct_commands._shell_quote("it's") == "'it'\\''s'"

    def test_with_spaces(self):
        assert direct_commands._shell_quote("hello world") == "'hello world'"


class TestSshRunResolvesCanastaHostName:
    """_ssh_run must translate canasta short names from hosts.yml into
    their actual SSH targets before invoking ssh, so commands like
    `canasta argocd password --host node1` don't hit `ssh: Could not
    resolve hostname node1`."""

    def test_short_name_resolved_via_hosts_yml(self, monkeypatch):
        captured = {}

        monkeypatch.setattr(
            direct_commands, "_read_hosts_yml",
            lambda: {"all": {"hosts": {"node1": {
                "ansible_host": "cp.example.com",
                "ansible_user": "admin",
            }}}},
        )

        def fake_run(full_cmd, **kwargs):
            captured["cmd"] = full_cmd
            return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc, _ = direct_commands._ssh_run("node1", "echo hi")
        assert rc == 0
        # ssh target must be admin@cp.example.com, not the bare 'node1'.
        ssh_target = captured["cmd"][-2]
        assert ssh_target == "admin@cp.example.com"

    def test_unregistered_host_passes_through(self, monkeypatch):
        captured = {}

        monkeypatch.setattr(direct_commands, "_read_hosts_yml", lambda: None)

        def fake_run(full_cmd, **kwargs):
            captured["cmd"] = full_cmd
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        direct_commands._ssh_run("cp.example.com", "echo hi")
        assert captured["cmd"][-2] == "cp.example.com"

    def test_user_at_host_passes_through(self, monkeypatch):
        """When hosts.yml has no entry but the input is already a
        user@host string, leave it alone."""
        captured = {}
        monkeypatch.setattr(direct_commands, "_read_hosts_yml", lambda: None)

        def fake_run(full_cmd, **kwargs):
            captured["cmd"] = full_cmd
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        direct_commands._ssh_run("admin@cp.example.com", "echo hi")
        assert captured["cmd"][-2] == "admin@cp.example.com"


class TestCheckDirExists:
    def test_local_exists(self, tmp_path):
        d = tmp_path / "exists"
        d.mkdir()
        assert direct_commands._check_dir_exists(str(d), "localhost")

    def test_local_missing(self, tmp_path):
        assert not direct_commands._check_dir_exists(str(tmp_path / "nope"), "localhost")


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

        monkeypatch.setattr(direct_commands, "_ssh_run", fake_ssh)
        monkeypatch.setattr(subprocess, "run", fail_subprocess)
        assert direct_commands._check_running_k8s("mywiki", "node1")
        assert called["host"] == "node1"
        assert "kubectl" in called["cmd"]
        assert "canasta-mywiki-web" in called["cmd"]
        assert "canasta-mywiki" in called["cmd"]  # namespace

    def test_remote_not_running(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, ""),
        )
        assert not direct_commands._check_running_k8s("mywiki", "node1")

    def test_remote_ssh_failure_treated_as_stopped(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (255, ""),  # SSH could not connect
        )
        assert not direct_commands._check_running_k8s("mywiki", "node1")


class TestCheckRunningCompose:
    def test_running(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})(),
        )
        assert direct_commands._check_running_compose(str(tmp_path), "localhost")

    def test_not_running(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": ""})(),
        )
        assert not direct_commands._check_running_compose(str(tmp_path), "localhost")

    def test_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": ""})(),
        )
        assert not direct_commands._check_running_compose(str(tmp_path), "localhost")


# ---------------------------------------------------------------------------
# Table formatting tests
# ---------------------------------------------------------------------------

class TestPrintTable:
    def test_single_instance(self, capsys):
        details = [{
            "id": "mysite",
            "host": "localhost",
            "path": "/srv/canasta/mysite",
            "orchestrator": "COMPOSE",
            "status": "RUNNING",
            "wikis": [
                {"id": "main", "url": "example.com"},
                {"id": "docs", "url": "example.com/docs"},
            ],
        }]
        direct_commands._print_table(details)
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert "Canasta ID" in lines[0]
        assert "Host" in lines[0]
        assert "Instance Path" in lines[0]
        assert "Orchestrator" in lines[1]
        assert "Wiki ID" in lines[2]
        assert "\u2500" in lines[3]
        assert "mysite" in lines[4]
        assert "COMPOSE" in lines[5]
        assert "RUNNING" in lines[5]
        assert "example.com/" in lines[6]
        assert "example.com/docs" in lines[7]

    def test_trailing_slash_added(self, capsys):
        details = [{
            "id": "s",
            "host": "localhost",
            "path": "/p",
            "orchestrator": "COMPOSE",
            "status": "STOPPED",
            "wikis": [{"id": "main", "url": "example.com"}],
        }]
        direct_commands._print_table(details)
        out = capsys.readouterr().out
        assert "example.com/" in out

    def test_url_with_path_no_extra_slash(self, capsys):
        details = [{
            "id": "s",
            "host": "localhost",
            "path": "/p",
            "orchestrator": "COMPOSE",
            "status": "STOPPED",
            "wikis": [{"id": "docs", "url": "example.com/docs"}],
        }]
        direct_commands._print_table(details)
        out = capsys.readouterr().out
        assert "example.com/docs" in out
        assert "example.com/docs/" not in out

    def test_no_wikis(self, capsys):
        details = [{
            "id": "s",
            "host": "localhost",
            "path": "/p",
            "orchestrator": "COMPOSE",
            "status": "NOT FOUND",
            "wikis": [],
        }]
        direct_commands._print_table(details)
        out = capsys.readouterr().out
        assert "(no wikis)" in out

    def test_multiple_instances_separated(self, capsys):
        details = [
            {
                "id": "a", "host": "localhost", "path": "/a",
                "orchestrator": "COMPOSE", "status": "RUNNING", "wikis": [],
            },
            {
                "id": "b", "host": "localhost", "path": "/b",
                "orchestrator": "COMPOSE", "status": "STOPPED", "wikis": [],
            },
        ]
        direct_commands._print_table(details)
        out = capsys.readouterr().out
        lines = out.split("\n")
        # Find the data lines — blank line separates instances
        data_start = 4  # after 3 header lines + separator
        # After first instance's 3 lines (id, orch, wikis), expect blank
        assert lines[data_start + 3] == ""

    def test_host_column_adapts_to_long_hostname(self, capsys):
        details = [{
            "id": "s",
            "host": "very-long-hostname.example.com",
            "path": "/p",
            "orchestrator": "COMPOSE",
            "status": "RUNNING",
            "wikis": [],
        }]
        direct_commands._print_table(details)
        out = capsys.readouterr().out
        header = out.split("\n")[0]
        host_start = header.index("Host")
        path_start = header.index("Instance Path")
        col_width = path_start - host_start
        assert col_width >= len("very-long-hostname.example.com") + 2


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_removes_stale_entries(self, registry):
        tmp_path, data = registry
        conf = str(tmp_path / "conf.json")
        # Remove siteB's directory
        import shutil
        shutil.rmtree(str(tmp_path / "siteB"))

        # Simulate cleanup
        instances = direct_commands._read_registry(conf)
        to_remove = [
            iid for iid, inst in instances.items()
            if not os.path.isdir(inst.get("path", ""))
        ]
        assert "siteB" in to_remove
        assert "siteA" not in to_remove

    def test_cleanup_writes_back(self, registry, capsys):
        tmp_path, _ = registry
        import shutil
        shutil.rmtree(str(tmp_path / "siteB"))

        args = type("Args", (), {"cleanup": True, "host": None})()
        direct_commands.cmd_list(args)

        # Verify siteB was removed from conf.json
        instances = direct_commands._read_registry(str(tmp_path / "conf.json"))
        assert "siteB" not in instances
        assert "siteA" in instances
        out = capsys.readouterr().out
        assert "Removed stale entries" in out

    def test_cleanup_keeps_unreachable_remote_by_default(
        self, registry_with_remote, monkeypatch, capsys
    ):
        """Remote entry whose SSH probe fails (rc=255) must not be
        auto-removed — it's 'unreachable', not 'missing'."""
        tmp_path, _ = registry_with_remote
        # Simulate SSH transport failure for the remote host
        monkeypatch.setattr(
            direct_commands, "_ssh_run", lambda host, cmd: (255, ""),
        )

        args = type("Args", (), {
            "cleanup": True, "host": None, "force": False, "dry_run": False,
        })()
        direct_commands.cmd_list(args)

        instances = direct_commands._read_registry(
            str(tmp_path / "conf.json")
        )
        assert "remote" in instances  # kept despite being unreachable
        assert "local" in instances
        out = capsys.readouterr().out
        assert "Kept" in out
        assert "unreachable" in out

    def test_cleanup_removes_unreachable_with_force(
        self, registry_with_remote, monkeypatch, capsys
    ):
        tmp_path, _ = registry_with_remote
        monkeypatch.setattr(
            direct_commands, "_ssh_run", lambda host, cmd: (255, ""),
        )

        args = type("Args", (), {
            "cleanup": True, "host": None, "force": True, "dry_run": False,
        })()
        direct_commands.cmd_list(args)

        instances = direct_commands._read_registry(
            str(tmp_path / "conf.json")
        )
        assert "remote" not in instances  # removed by --force
        assert "local" in instances

    def test_cleanup_removes_confirmed_missing_remote(
        self, registry_with_remote, monkeypatch, capsys
    ):
        """SSH succeeds but 'test -d' returns non-zero (dir really is
        gone on the remote host) — safe to remove without --force."""
        tmp_path, _ = registry_with_remote
        # rc=1 means test -d failed; SSH itself was fine
        monkeypatch.setattr(
            direct_commands, "_ssh_run", lambda host, cmd: (1, ""),
        )

        args = type("Args", (), {
            "cleanup": True, "host": None, "force": False, "dry_run": False,
        })()
        direct_commands.cmd_list(args)

        instances = direct_commands._read_registry(
            str(tmp_path / "conf.json")
        )
        assert "remote" not in instances
        assert "local" in instances
        out = capsys.readouterr().out
        assert "Removed stale entries" in out

    def test_cleanup_dry_run_does_not_modify(
        self, registry_with_remote, monkeypatch, capsys
    ):
        tmp_path, _ = registry_with_remote
        monkeypatch.setattr(
            direct_commands, "_ssh_run", lambda host, cmd: (1, ""),
        )

        args = type("Args", (), {
            "cleanup": True, "host": None, "force": False, "dry_run": True,
        })()
        direct_commands.cmd_list(args)

        # Registry unchanged
        instances = direct_commands._read_registry(
            str(tmp_path / "conf.json")
        )
        assert "remote" in instances
        assert "local" in instances
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "Would remove" in out


# ---------------------------------------------------------------------------
# End-to-end cmd_list tests
# ---------------------------------------------------------------------------

class TestGatherInstanceInfo:
    def test_local_instance(self, registry, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_check_running",
            lambda *a, **kw: True,
        )
        _, data = registry
        detail = direct_commands._gather_instance_info(
            "siteA", data["Instances"]["siteA"],
        )
        assert detail["id"] == "siteA"
        assert detail["status"] == "RUNNING"
        assert len(detail["wikis"]) == 2

    def test_local_missing_dir(self, tmp_path):
        inst = {"path": str(tmp_path / "gone"), "orchestrator": "compose"}
        detail = direct_commands._gather_instance_info("x", inst)
        assert detail["status"] == "NOT FOUND"
        assert detail["wikis"] == []

    def test_remote_batched_ssh_running(self, monkeypatch):
        wikis_yaml = "wikis:\n  - id: main\n    url: example.com\n"
        ssh_output = (
            "DIR_OK\n"
            + direct_commands._SENTINEL + "\n"
            + wikis_yaml
            + direct_commands._SENTINEL + "\n"
            + "abc123\n"
        )
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, ssh_output),
        )
        inst = {
            "path": "/srv/site",
            "orchestrator": "compose",
            "host": "admin@remote.example.com",
        }
        detail = direct_commands._gather_instance_info("site", inst)
        assert detail["status"] == "RUNNING"
        assert detail["host"] == "admin@remote.example.com"
        assert len(detail["wikis"]) == 1
        assert detail["wikis"][0]["id"] == "main"

    def test_remote_batched_ssh_stopped(self, monkeypatch):
        ssh_output = (
            "DIR_OK\n"
            + direct_commands._SENTINEL + "\n"
            + "WIKIS_MISSING\n"
            + direct_commands._SENTINEL + "\n"
            + "\n"
        )
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, ssh_output),
        )
        inst = {
            "path": "/srv/site",
            "orchestrator": "compose",
            "host": "remote",
        }
        detail = direct_commands._gather_instance_info("site", inst)
        assert detail["status"] == "STOPPED"
        assert detail["wikis"] == []

    def test_remote_dir_missing(self, monkeypatch):
        ssh_output = (
            "DIR_MISSING\n"
            + direct_commands._SENTINEL + "\n"
            + "WIKIS_MISSING\n"
            + direct_commands._SENTINEL + "\n"
            + "\n"
        )
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, ssh_output),
        )
        inst = {
            "path": "/srv/gone",
            "orchestrator": "compose",
            "host": "remote",
        }
        detail = direct_commands._gather_instance_info("x", inst)
        assert detail["status"] == "NOT FOUND"

    def test_remote_ssh_failure(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (255, ""),
        )
        inst = {
            "path": "/srv/site",
            "orchestrator": "compose",
            "host": "unreachable",
        }
        detail = direct_commands._gather_instance_info("x", inst)
        assert detail["status"] == "NOT FOUND"


class TestGatherAllInstances:
    def test_parallel_execution(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_gather_instance_info",
            lambda iid, inst: direct_commands._make_detail(
                iid, "localhost", inst["path"], "compose", "STOPPED", [],
            ),
        )
        instances = {
            "a": {"path": "/a", "orchestrator": "compose"},
            "b": {"path": "/b", "orchestrator": "compose"},
        }
        results = direct_commands._gather_all_instances(instances)
        assert len(results) == 2
        assert results[0]["id"] == "a"
        assert results[1]["id"] == "b"

    def test_preserves_order(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_gather_instance_info",
            lambda iid, inst: direct_commands._make_detail(
                iid, "localhost", "/p", "compose", "STOPPED", [],
            ),
        )
        instances = {
            "z": {"path": "/z", "orchestrator": "compose"},
            "a": {"path": "/a", "orchestrator": "compose"},
            "m": {"path": "/m", "orchestrator": "compose"},
        }
        results = direct_commands._gather_all_instances(instances)
        assert [r["id"] for r in results] == ["z", "a", "m"]

    def test_handles_exception(self, monkeypatch):
        def flaky(iid, inst):
            if iid == "bad":
                raise RuntimeError("boom")
            return direct_commands._make_detail(
                iid, "localhost", "/p", "compose", "STOPPED", [],
            )

        monkeypatch.setattr(
            direct_commands, "_gather_instance_info", flaky,
        )
        instances = {
            "good": {"path": "/good", "orchestrator": "compose"},
            "bad": {"path": "/bad", "orchestrator": "compose"},
        }
        results = direct_commands._gather_all_instances(instances)
        assert results[0]["status"] == "STOPPED"
        assert results[1]["status"] == "ERROR"

    def test_empty(self):
        assert direct_commands._gather_all_instances({}) == []


class TestCmdList:
    def test_empty_registry(self, tmp_path, monkeypatch, capsys):
        conf = tmp_path / "conf.json"
        conf.write_text(json.dumps({"Instances": {}}))
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))

        args = type("Args", (), {"cleanup": False, "host": None})()
        rc = direct_commands.cmd_list(args)
        assert rc == 0
        assert "No Canasta instances." in capsys.readouterr().out

    def test_no_registry_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))

        args = type("Args", (), {"cleanup": False, "host": None})()
        rc = direct_commands.cmd_list(args)
        assert rc == 0
        assert "No Canasta instances." in capsys.readouterr().out

    def test_lists_instances_with_wikis(self, registry, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_check_running",
            lambda *a, **kw: False,
        )
        args = type("Args", (), {"cleanup": False, "host": None})()
        rc = direct_commands.cmd_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "siteA" in out
        assert "siteB" in out
        assert "example.com/" in out
        assert "STOPPED" in out

    def test_running_instance(self, registry, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_check_running",
            lambda *a, **kw: True,
        )
        args = type("Args", (), {"cleanup": False, "host": None})()
        rc = direct_commands.cmd_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "RUNNING" in out

    def test_host_filter(self, registry_with_remote, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_gather_instance_info",
            lambda iid, inst: direct_commands._make_detail(
                iid,
                inst.get("host") or "localhost",
                inst.get("path", ""),
                inst.get("orchestrator", "compose"),
                "STOPPED",
                [],
            ),
        )
        args = type("Args", (), {
            "cleanup": False,
            "host": "prod.example.com",
        })()
        rc = direct_commands.cmd_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "remote" in out
        assert "local" not in out

    def test_missing_dir_shows_not_found(self, tmp_path, monkeypatch, capsys):
        data = {
            "Instances": {
                "gone": {
                    "id": "gone",
                    "path": str(tmp_path / "nonexistent"),
                    "orchestrator": "compose",
                },
            }
        }
        conf = tmp_path / "conf.json"
        conf.write_text(json.dumps(data))
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))

        args = type("Args", (), {"cleanup": False, "host": None})()
        rc = direct_commands.cmd_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "NOT FOUND" in out
        assert "(no wikis)" in out


# ---------------------------------------------------------------------------
# Version command tests
# ---------------------------------------------------------------------------

class TestCmdVersion:
    def test_registered(self):
        assert direct_commands.is_direct_command("version")

    def test_native_checkout(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "VERSION").write_text("4.0.0\n")
        monkeypatch.delenv("CANASTA_RUN_MODE", raising=False)
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0,
                "stdout": "abc1234\n" if "rev-parse" in a[0] else "2026-04-18 12:00:00\n",
            })(),
        )
        rc = direct_commands.cmd_version(None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "v4.0.0" in out
        assert "native" in out
        assert "abc1234" in out

    def test_docker_mode(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "VERSION").write_text("4.0.0\n")
        (tmp_path / "BUILD_COMMIT").write_text("def5678\n")
        (tmp_path / "BUILD_DATE").write_text("2026-04-18 10:00:00\n")
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(tmp_path))
        rc = direct_commands.cmd_version(None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "v4.0.0" in out
        assert "docker" in out
        assert "def5678" in out

    def test_target_canasta_version_line(self, tmp_path, monkeypatch, capsys):
        """When CANASTA_VERSION file is present, the target version line
        is always printed, even without -i."""
        (tmp_path / "VERSION").write_text("4.0.0\n")
        (tmp_path / "CANASTA_VERSION").write_text("3.5.7\n")
        (tmp_path / "BUILD_COMMIT").write_text("abc1234\n")
        (tmp_path / "BUILD_DATE").write_text("2026-04-23 00:00:00\n")
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(tmp_path))
        # No instances registered — PWD auto-resolve should be a no-op.
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        args = type("Args", (), {"id": None, "cli_only": False, "host": None})()
        rc = direct_commands.cmd_version(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Target Canasta version: 3.5.7" in out

    def test_missing_canasta_version_file(self, tmp_path, monkeypatch, capsys):
        """If CANASTA_VERSION file is absent, target shown as 'unknown'
        (doesn't crash)."""
        (tmp_path / "VERSION").write_text("4.0.0\n")
        (tmp_path / "BUILD_COMMIT").write_text("abc1234\n")
        (tmp_path / "BUILD_DATE").write_text("2026-04-23 00:00:00\n")
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(tmp_path))
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        args = type("Args", (), {"id": None, "cli_only": False, "host": None})()
        rc = direct_commands.cmd_version(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Target Canasta version: unknown" in out

    def test_native_with_build_files(self, tmp_path, monkeypatch, capsys):
        """Native installs also write BUILD_COMMIT/BUILD_DATE (via
        get-canasta.sh or make build-info). The presence of those
        files must not cause mode to be misreported as docker."""
        (tmp_path / "VERSION").write_text("4.0.0\n")
        (tmp_path / "BUILD_COMMIT").write_text("abc1234\n")
        (tmp_path / "BUILD_DATE").write_text("2026-04-20 14:00:00\n")
        monkeypatch.delenv("CANASTA_RUN_MODE", raising=False)
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(tmp_path))
        rc = direct_commands.cmd_version(None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "v4.0.0" in out
        assert "native" in out
        assert "abc1234" in out

    def test_missing_version_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": ""})(),
        )
        rc = direct_commands.cmd_version(None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "unknown" in out

    def test_not_a_git_repo(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "VERSION").write_text("4.0.0\n")
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {"returncode": 128, "stdout": ""})(),
        )
        rc = direct_commands.cmd_version(None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "v4.0.0" in out
        assert "unknown" in out


class TestCmdVersionInstanceModes:
    """Behavioral coverage for the three instance-reporting modes added
    in the version redesign: --cli-only, cwd-resolve-inside, and
    list-all-outside. Uses a fake script_dir + registry so the header
    and instance lookups can be exercised end-to-end without real
    Canasta installs."""

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
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(script_dir))
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(config_dir))
        return script_dir, config_dir

    def test_cli_only_skips_instance_reads(self, tmp_path, monkeypatch, capsys):
        """--cli-only short-circuits after the two header lines, even
        when instances are registered and cwd would match one."""
        site = tmp_path / "site"
        site.mkdir()
        (site / ".env").write_text("CANASTA_IMAGE=canasta:3.6.3\n")
        self._setup(tmp_path, monkeypatch, {
            "site": {"path": str(site), "host": "localhost"},
        })
        monkeypatch.chdir(site)  # inside the instance dir
        args = type("Args", (), {"id": None, "cli_only": True, "host": None})()
        rc = direct_commands.cmd_version(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Canasta CLI v4.0.0" in out
        assert "Target Canasta version: 3.6.3" in out
        # No instance line — cli_only must short-circuit.
        assert "Instance '" not in out

    def test_outside_instance_lists_all_without_running_query(
        self, tmp_path, monkeypatch, capsys
    ):
        """Outside any instance directory, the default lists every
        registered instance's pinned CANASTA_IMAGE tag but skips the
        docker-compose-exec runtime query (keeps the default fast)."""
        s1 = tmp_path / "site1"
        s1.mkdir()
        (s1 / ".env").write_text("CANASTA_IMAGE=canasta:3.6.0\n")
        s2 = tmp_path / "site2"
        s2.mkdir()
        (s2 / ".env").write_text("CANASTA_IMAGE=canasta:3.6.3\n")
        self._setup(tmp_path, monkeypatch, {
            "site1": {"path": str(s1), "host": "localhost"},
            "site2": {"path": str(s2), "host": "localhost"},
        })
        # Sit somewhere that is not inside either instance dir.
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)
        args = type("Args", (), {"id": None, "cli_only": False, "host": None})()
        rc = direct_commands.cmd_version(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Instance 'site1': canasta:3.6.0" in out
        assert "Instance 'site2': canasta:3.6.3" in out
        # List-all must NOT include '(running: ...)' — that suffix
        # comes from _read_instance_image's runtime query, which
        # is intentionally skipped for the fast default.
        assert "running:" not in out

    def test_inside_instance_dir_shows_current_full(
        self, tmp_path, monkeypatch, capsys
    ):
        """Inside an instance directory with no -i, should print the
        full (image + running) line for that instance only."""
        site = tmp_path / "site"
        site.mkdir()
        (site / ".env").write_text("CANASTA_IMAGE=canasta:3.6.3\n")
        self._setup(tmp_path, monkeypatch, {
            "site": {"path": str(site), "host": "localhost"},
            "other": {"path": str(tmp_path / "other"), "host": "localhost"},
        })
        monkeypatch.chdir(site)
        # Stub the runtime-version query to return a known string so
        # the test doesn't depend on docker being available.
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "3.6.3-running\n",
            })(),
        )
        args = type("Args", (), {"id": None, "cli_only": False, "host": None})()
        rc = direct_commands.cmd_version(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Instance 'site': canasta:3.6.3 (running: 3.6.3-running)" in out
        # The sibling instance must not appear — cwd resolution wins
        # and suppresses the list-all fallback.
        assert "'other'" not in out

    def test_all_flag_rejected(self, tmp_path, monkeypatch, capsys):
        """--all was removed; argparse should reject it so anyone who
        still uses it gets a loud error instead of silently falling
        through to the default."""
        import canasta
        parser = canasta.build_parser(canasta.load_definitions())
        with pytest.raises(SystemExit):
            parser.parse_args(["version", "--all"])


# ---------------------------------------------------------------------------
# .env parsing tests
# ---------------------------------------------------------------------------

class TestReadEnvFile:
    def test_reads_env(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            "# comment\n"
            "MW_SITE_SERVER=https://example.com\n"
            'MW_SITE_NAME="My Wiki"\n'
            "EMPTY=\n"
        )
        result = direct_commands._read_env_file(str(tmp_path), "localhost")
        assert result["MW_SITE_SERVER"] == "https://example.com"
        assert result["MW_SITE_NAME"] == "My Wiki"
        assert result["EMPTY"] == ""

    def test_handles_equals_in_value(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("KEY=a=b=c\n")
        result = direct_commands._read_env_file(str(tmp_path), "localhost")
        assert result["KEY"] == "a=b=c"

    def test_missing_file(self, tmp_path):
        result = direct_commands._read_env_file(str(tmp_path), "localhost")
        assert result == {}

    def test_single_quoted_value(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("KEY='hello world'\n")
        result = direct_commands._read_env_file(str(tmp_path), "localhost")
        assert result["KEY"] == "hello world"


# ---------------------------------------------------------------------------
# config get tests
# ---------------------------------------------------------------------------

class TestCmdConfigGet:
    def test_registered(self):
        assert direct_commands.is_direct_command("config_get")

    def test_get_single_key(self, tmp_path, monkeypatch, capsys):
        env = tmp_path / ".env"
        env.write_text("MW_SITE_SERVER=https://example.com\nOTHER=value\n")
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {"path": str(tmp_path), "orchestrator": "compose"}),
        )
        args = type("Args", (), {
            "id": "test", "keys": ["MW_SITE_SERVER"], "force": False,
        })()
        rc = direct_commands.cmd_config_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "MW_SITE_SERVER=https://example.com"

    def test_get_multiple_keys(self, tmp_path, monkeypatch, capsys):
        env = tmp_path / ".env"
        env.write_text("A=1\nB=2\nC=3\n")
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {"path": str(tmp_path), "orchestrator": "compose"}),
        )
        args = type("Args", (), {
            "id": "test", "keys": ["A", "C"], "force": False,
        })()
        rc = direct_commands.cmd_config_get(args)
        assert rc == 0
        out = capsys.readouterr().out.strip().splitlines()
        assert out == ["A=1", "C=3"]

    def test_get_missing_key(self, tmp_path, monkeypatch, capsys):
        env = tmp_path / ".env"
        env.write_text("MW_SITE_SERVER=https://example.com\n")
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {"path": str(tmp_path), "orchestrator": "compose"}),
        )
        args = type("Args", (), {
            "id": "test", "keys": ["NOPE"], "force": False,
        })()
        rc = direct_commands.cmd_config_get(args)
        assert rc == 0
        assert "not found" in capsys.readouterr().out.lower()

    def test_get_all_sorted(self, tmp_path, monkeypatch, capsys):
        env = tmp_path / ".env"
        env.write_text("ZEBRA=z\nAPPLE=a\nMIDDLE=m\n")
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {"path": str(tmp_path), "orchestrator": "compose"}),
        )
        args = type("Args", (), {"id": "test", "key": None, "force": False})()
        rc = direct_commands.cmd_config_get(args)
        assert rc == 0
        lines = capsys.readouterr().out.strip().split("\n")
        assert lines[0] == "APPLE=a"
        assert lines[1] == "MIDDLE=m"
        assert lines[2] == "ZEBRA=z"

    def test_no_env_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {"path": str(tmp_path), "orchestrator": "compose"}),
        )
        args = type("Args", (), {"id": "test", "key": None, "force": False})()
        rc = direct_commands.cmd_config_get(args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Compose file args tests
# ---------------------------------------------------------------------------

class TestComposeFileArgs:
    def test_base_only(self, tmp_path):
        args = direct_commands._compose_file_args(str(tmp_path), "localhost")
        assert args == ["-f", "docker-compose.yml"]

    def test_with_override(self, tmp_path):
        (tmp_path / "docker-compose.override.yml").write_text("")
        args = direct_commands._compose_file_args(str(tmp_path), "localhost")
        assert args == [
            "-f", "docker-compose.yml",
            "-f", "docker-compose.override.yml",
        ]

    def test_with_devmode(self, tmp_path):
        args = direct_commands._compose_file_args(
            str(tmp_path), "localhost", devmode=True,
        )
        assert "-f" in args
        assert "docker-compose.dev.yml" in args


# ---------------------------------------------------------------------------
# Start / stop / restart tests
# ---------------------------------------------------------------------------

class TestLifecycleCommands:
    def test_start_registered(self):
        assert direct_commands.is_direct_command("start")

    def test_stop_registered(self):
        assert direct_commands.is_direct_command("stop")

    def test_restart_registered(self):
        assert direct_commands.is_direct_command("restart")

    def test_k8s_start_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("k8s-site", {
                "path": "/srv/k8s-site",
                "orchestrator": "kubernetes",
            }),
        )
        args = type("Args", (), {"id": "k8s-site"})()
        assert direct_commands.cmd_start(args) is direct_commands.FALLBACK

    def test_k8s_restart_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("k8s-site", {
                "path": "/srv/k8s-site",
                "orchestrator": "kubernetes",
            }),
        )
        args = type("Args", (), {"id": "k8s-site"})()
        assert direct_commands.cmd_restart(args) is direct_commands.FALLBACK

    def test_k8s_stop_runs_kubectl(self, monkeypatch):
        kubectl_cmds = []

        def mock_run(cmd, **kw):
            kubectl_cmds.append(cmd)
            # Return a STS when queried so the scale-sts step runs
            if "statefulset" in cmd and "-o" in cmd and "name" in cmd:
                return type("R", (), {
                    "returncode": 0,
                    "stdout": "statefulset.apps/foo-db\n",
                    "stderr": "",
                })()
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("k8s-site", {
                "path": "/srv/k8s-site",
                "orchestrator": "kubernetes",
            }),
        )
        args = type("Args", (), {"id": "k8s-site"})()
        rc = direct_commands.cmd_stop(args)
        assert rc == 0
        cmds_str = str(kubectl_cmds)
        assert "scale" in cmds_str
        assert "replicas=0" in cmds_str

    def test_k8s_stop_skips_sts_scale_when_none_exist(self, monkeypatch):
        # External-DB instances with Elasticsearch disabled have zero
        # StatefulSets. 'kubectl scale statefulset --all' errors with
        # "no objects passed to scale" in that case — we must check
        # first and skip the scale step.
        kubectl_cmds = []

        def mock_run(cmd, **kw):
            kubectl_cmds.append(cmd)
            # "No Argo CD application" + "no statefulsets" scenario
            if "application" in cmd:
                return type("R", (), {
                    "returncode": 1, "stdout": "", "stderr": "not found",
                })()
            if "statefulset" in cmd and "-o" in cmd and "name" in cmd:
                return type("R", (), {
                    "returncode": 0, "stdout": "", "stderr": "",
                })()
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("extdb-site", {
                "path": "/srv/extdb-site",
                "orchestrator": "kubernetes",
            }),
        )
        args = type("Args", (), {"id": "extdb-site"})()
        rc = direct_commands.cmd_stop(args)
        assert rc == 0
        # Deployment scale must have happened...
        deployment_scale = [
            c for c in kubectl_cmds
            if "scale" in c and "deployment" in c
        ]
        assert deployment_scale, "deployment scale should still run"
        # ... but STS scale must NOT, since 'kubectl get statefulset'
        # returned no objects.
        sts_scale = [
            c for c in kubectl_cmds
            if "scale" in c and "statefulset" in c
        ]
        assert not sts_scale, (
            "statefulset scale must be skipped when no STS exist "
            "(would error 'no objects passed to scale')"
        )

    def test_start_runs_up(self, monkeypatch):
        captured_cmds = []

        def mock_run(cmd, **kw):
            captured_cmds.append(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_compose_file_args",
            lambda *a, **kw: ["-f", "docker-compose.yml"],
        )

        args = type("Args", (), {"id": "test"})()
        rc = direct_commands.cmd_start(args)
        assert rc == 0
        assert captured_cmds[0] == [
            "docker", "compose", "-f", "docker-compose.yml", "up", "-d",
        ]

    def test_stop_runs_down(self, monkeypatch):
        captured_cmds = []

        def mock_run(cmd, **kw):
            captured_cmds.append(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_compose_file_args",
            lambda *a, **kw: ["-f", "docker-compose.yml"],
        )

        args = type("Args", (), {"id": "test"})()
        rc = direct_commands.cmd_stop(args)
        assert rc == 0
        assert "down" in captured_cmds[0]

    def test_restart_runs_down_then_up(self, monkeypatch):
        captured_cmds = []

        def mock_run(cmd, **kw):
            captured_cmds.append(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_compose_file_args",
            lambda *a, **kw: ["-f", "docker-compose.yml"],
        )

        args = type("Args", (), {"id": "test"})()
        rc = direct_commands.cmd_restart(args)
        assert rc == 0
        assert len(captured_cmds) == 2
        assert "down" in captured_cmds[0]
        assert "up" in captured_cmds[1]

    def test_restart_stops_on_down_failure(self, monkeypatch):
        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            return type("R", (), {"returncode": 1})()

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_compose_file_args",
            lambda *a, **kw: ["-f", "docker-compose.yml"],
        )

        args = type("Args", (), {"id": "test"})()
        rc = direct_commands.cmd_restart(args)
        assert rc == 1
        assert call_count[0] == 1

    def test_remote_start_uses_ssh(self, monkeypatch):
        ssh_cmds = []

        def mock_ssh(host, cmd):
            ssh_cmds.append((host, cmd))
            # Return empty .env for the sync-profiles read
            return 0, ""

        monkeypatch.setattr(direct_commands, "_ssh_run", mock_ssh)
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
                "host": "admin@remote",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_compose_file_args",
            lambda *a, **kw: ["-f", "docker-compose.yml"],
        )

        args = type("Args", (), {"id": "test"})()
        rc = direct_commands.cmd_start(args)
        assert rc == 0
        # The sync-profiles read + the up -d: 2 SSH calls total.
        # Empty .env returned above → no write.
        up_calls = [c for _, c in ssh_cmds if "up -d" in c]
        assert len(up_calls) == 1
        assert all(h == "admin@remote" for h, _ in ssh_cmds)


class TestEnvFileWriter:
    """_parse_env_entries + _set_env_entry + _entries_to_content round trip."""

    def test_round_trip_preserves_comments_and_blanks(self):
        content = "# header comment\n\nKEY=value\n# inline\nOTHER=x\n"
        entries = direct_commands._parse_env_entries(content)
        out = direct_commands._entries_to_content(entries)
        assert out == content.rstrip("\n") or out == content
        # Specifically: comment + blank + entry + comment + entry
        assert entries[0] == (None, "# header comment", True)
        assert entries[1] == (None, "", True)
        assert entries[2] == ("KEY", "value", False)
        assert entries[3] == (None, "# inline", True)
        assert entries[4] == ("OTHER", "x", False)

    def test_parse_strips_matching_quotes(self):
        entries = direct_commands._parse_env_entries(
            'A="quoted"\nB=\'single\'\nC=bare\n'
        )
        vals = {k: v for k, v, c in entries if not c}
        assert vals == {"A": "quoted", "B": "single", "C": "bare"}

    def test_set_updates_first_occurrence_and_dedupes(self):
        entries = direct_commands._parse_env_entries("K=old\nK=dup\nX=y\n")
        new = direct_commands._set_env_entry(entries, "K", "new")
        out = direct_commands._entries_to_content(new)
        # First occurrence updated, duplicate dropped, other untouched
        assert out.split("\n") == ["K=new", "X=y", ""]

    def test_set_appends_when_absent(self):
        entries = direct_commands._parse_env_entries("A=1\n")
        new = direct_commands._set_env_entry(entries, "B", "2")
        out = direct_commands._entries_to_content(new)
        assert out == "A=1\n\nB=2" or out.endswith("B=2")
        assert "B=2" in out

    def test_write_env_content_local(self, tmp_path):
        p = str(tmp_path)
        ok = direct_commands._write_env_content(p, "localhost", "K=v\n")
        assert ok is True
        with open(tmp_path / ".env") as f:
            assert f.read() == "K=v\n"


class TestSyncComposeProfiles:
    def _inst(self, tmp_path):
        return {"path": str(tmp_path), "host": "localhost"}

    def test_adds_elasticsearch_when_flag_true(self, tmp_path):
        # User toggled CANASTA_ENABLE_ELASTICSEARCH=true by hand-editing
        # .env. COMPOSE_PROFILES hasn't been updated. _sync should fix it.
        (tmp_path / ".env").write_text(
            "CANASTA_ENABLE_ELASTICSEARCH=true\nCOMPOSE_PROFILES=\n"
        )
        direct_commands._sync_compose_profiles(self._inst(tmp_path))
        content = (tmp_path / ".env").read_text()
        assert "COMPOSE_PROFILES=elasticsearch" in content

    def test_adds_varnish_by_default(self, tmp_path):
        # CANASTA_ENABLE_VARNISH defaults to true even when unset.
        (tmp_path / ".env").write_text("COMPOSE_PROFILES=\n")
        direct_commands._sync_compose_profiles(self._inst(tmp_path))
        content = (tmp_path / ".env").read_text()
        assert "COMPOSE_PROFILES=varnish" in content

    def test_removes_stale_managed_profile(self, tmp_path):
        # COMPOSE_PROFILES has 'elasticsearch' but the flag is false.
        (tmp_path / ".env").write_text(
            "CANASTA_ENABLE_ELASTICSEARCH=false\n"
            "CANASTA_ENABLE_VARNISH=false\n"
            "CANASTA_ENABLE_OBSERVABILITY=false\n"
            "COMPOSE_PROFILES=elasticsearch,varnish\n"
        )
        direct_commands._sync_compose_profiles(self._inst(tmp_path))
        content = (tmp_path / ".env").read_text()
        # Every managed profile is dropped because all flags are false
        for line in content.split("\n"):
            if line.startswith("COMPOSE_PROFILES="):
                assert line == "COMPOSE_PROFILES="
                break

    def test_preserves_unmanaged_profiles(self, tmp_path):
        # User has a custom profile 'mytest' not in the managed set.
        # It must survive the sync.
        (tmp_path / ".env").write_text(
            "CANASTA_ENABLE_VARNISH=false\n"
            "CANASTA_ENABLE_ELASTICSEARCH=false\n"
            "CANASTA_ENABLE_OBSERVABILITY=false\n"
            "COMPOSE_PROFILES=mytest,varnish\n"
        )
        direct_commands._sync_compose_profiles(self._inst(tmp_path))
        content = (tmp_path / ".env").read_text()
        for line in content.split("\n"):
            if line.startswith("COMPOSE_PROFILES="):
                assert "mytest" in line
                assert "varnish" not in line
                break

    def test_no_write_when_already_in_sync(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "CANASTA_ENABLE_VARNISH=true\nCOMPOSE_PROFILES=varnish\n"
        )
        original_mtime = env_path.stat().st_mtime_ns
        direct_commands._sync_compose_profiles(self._inst(tmp_path))
        # If _sync wrote the file, mtime would update. The guard in
        # _sync (sorted(desired) == sorted(current)) must prevent that.
        assert env_path.stat().st_mtime_ns == original_mtime

    def test_no_env_file_is_a_noop(self, tmp_path):
        # Nothing to sync; must not create a file or raise.
        direct_commands._sync_compose_profiles(self._inst(tmp_path))
        assert not (tmp_path / ".env").exists()

    def test_preserves_comments_on_write(self, tmp_path):
        (tmp_path / ".env").write_text(
            "# Canasta config\n"
            "CANASTA_ENABLE_VARNISH=true\n"
            "# profiles below\n"
            "COMPOSE_PROFILES=\n"
        )
        direct_commands._sync_compose_profiles(self._inst(tmp_path))
        content = (tmp_path / ".env").read_text()
        assert "# Canasta config" in content
        assert "# profiles below" in content


class TestDumpComposeFailure:
    def test_prints_ps_and_logs_to_stderr(self, monkeypatch, capsys):
        # Return deterministic output for both ps and logs commands.
        def fake_run(cmd, **kw):
            if "ps" in cmd:
                return type("R", (), {
                    "returncode": 0,
                    "stdout": "NAME STATUS\nweb-1 Restarting",
                    "stderr": "",
                })()
            return type("R", (), {
                "returncode": 0,
                "stdout": "web-1 | exec format error",
                "stderr": "",
            })()

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            direct_commands, "_compose_file_args",
            lambda *a, **kw: ["-f", "docker-compose.yml"],
        )
        direct_commands._dump_compose_failure({
            "path": "/srv/test", "host": "localhost",
        })
        err = capsys.readouterr().err
        assert "docker compose ps -a" in err
        assert "docker compose logs" in err
        assert "Restarting" in err
        assert "exec format error" in err


# ---------------------------------------------------------------------------
# Host command tests
# ---------------------------------------------------------------------------

class TestHostList:
    def test_registered(self):
        assert direct_commands.is_direct_command("host_list")

    def test_no_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        rc = direct_commands.cmd_host_list(None)
        assert rc == 0
        assert "No hosts configured" in capsys.readouterr().out

    def test_lists_hosts(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        hosts_file = tmp_path / "hosts.yml"
        hosts_file.write_text(
            "all:\n"
            "  hosts:\n"
            "    prod1:\n"
            "      ansible_host: prod1.example.com\n"
            "      ansible_user: ubuntu\n"
            "    prod2:\n"
            "      ansible_host: 10.0.0.5\n"
        )
        rc = direct_commands.cmd_host_list(None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "prod1" in out
        assert "prod1.example.com" in out
        assert "ubuntu" in out
        assert "prod2" in out

    def test_empty_hosts(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        hosts_file = tmp_path / "hosts.yml"
        hosts_file.write_text("all:\n  hosts: {}\n")
        rc = direct_commands.cmd_host_list(None)
        assert rc == 0
        assert "No hosts configured" in capsys.readouterr().out


class TestHostAdd:
    def test_registered(self):
        assert direct_commands.is_direct_command("host_add")

    def test_add_new_host(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        args = type("Args", (), {
            "host_name": "prod1",
            "ssh": "ubuntu@prod1.example.com",
            "python": None,
        })()
        rc = direct_commands.cmd_host_add(args)
        assert rc == 0
        assert "saved" in capsys.readouterr().out

        import yaml as _yaml
        with open(tmp_path / "hosts.yml") as f:
            data = _yaml.safe_load(f)
        assert data["all"]["hosts"]["prod1"]["ansible_host"] == "prod1.example.com"
        assert data["all"]["hosts"]["prod1"]["ansible_user"] == "ubuntu"

    def test_add_with_python(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        args = type("Args", (), {
            "host_name": "prod1",
            "ssh": "10.0.0.5",
            "python": "/usr/bin/python3",
        })()
        rc = direct_commands.cmd_host_add(args)
        assert rc == 0

        import yaml as _yaml
        with open(tmp_path / "hosts.yml") as f:
            data = _yaml.safe_load(f)
        assert data["all"]["hosts"]["prod1"]["ansible_python_interpreter"] == "/usr/bin/python3"
        assert "ansible_user" not in data["all"]["hosts"]["prod1"]

    def test_update_existing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        hosts_file = tmp_path / "hosts.yml"
        hosts_file.write_text(
            "all:\n  hosts:\n    prod1:\n      ansible_host: old.com\n"
        )
        args = type("Args", (), {
            "host_name": "prod1",
            "ssh": "admin@new.com",
            "python": None,
        })()
        rc = direct_commands.cmd_host_add(args)
        assert rc == 0

        import yaml as _yaml
        with open(tmp_path / "hosts.yml") as f:
            data = _yaml.safe_load(f)
        assert data["all"]["hosts"]["prod1"]["ansible_host"] == "new.com"


class TestHostRemove:
    def test_registered(self):
        assert direct_commands.is_direct_command("host_remove")

    def test_remove_existing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        hosts_file = tmp_path / "hosts.yml"
        hosts_file.write_text(
            "all:\n  hosts:\n    prod1:\n      ansible_host: prod1.com\n"
            "    prod2:\n      ansible_host: prod2.com\n"
        )
        args = type("Args", (), {"host_name": "prod1"})()
        rc = direct_commands.cmd_host_remove(args)
        assert rc == 0
        assert "removed" in capsys.readouterr().out

        import yaml as _yaml
        with open(tmp_path / "hosts.yml") as f:
            data = _yaml.safe_load(f)
        assert "prod1" not in data["all"]["hosts"]
        assert "prod2" in data["all"]["hosts"]

    def test_remove_nonexistent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        hosts_file = tmp_path / "hosts.yml"
        hosts_file.write_text("all:\n  hosts:\n    prod1:\n      ansible_host: x\n")
        args = type("Args", (), {"host_name": "nope"})()
        rc = direct_commands.cmd_host_remove(args)
        assert rc == 1

    def test_remove_no_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        args = type("Args", (), {"host_name": "prod1"})()
        rc = direct_commands.cmd_host_remove(args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Gitops status tests
# ---------------------------------------------------------------------------

class TestParseGitopsStatus:
    def _make_output(self, hostname="myhost", hosts_yaml="MISSING",
                     commit="abc1234", applied="abc1234",
                     staged="", unstaged="", revcount="0\t0"):
        d = direct_commands._SENTINEL
        return (
            hostname + "\n" + d + "\n"
            + hosts_yaml + "\n" + d + "\n"
            + commit + "\n" + d + "\n"
            + applied + "\n" + d + "\n"
            + staged + "\n" + d + "\n"
            + unstaged + "\n" + d + "\n"
            + revcount + "\n"
        )

    def test_basic_status(self):
        out = self._make_output()
        result = direct_commands._parse_gitops_status(out, "mysite")
        assert "Host:           myhost" in result
        assert "Canasta ID:     mysite" in result
        assert "Current commit: abc1234" in result
        assert "No changes." in result
        assert "Up to date with remote." in result

    def test_with_staged_files(self):
        out = self._make_output(staged="config/.env\nconfig/wikis.yaml")
        result = direct_commands._parse_gitops_status(out, "mysite")
        assert "Staged for push (2 files):" in result
        assert "config/.env" in result
        assert "config/wikis.yaml" in result

    def test_with_unstaged_files(self):
        out = self._make_output(unstaged="docker-compose.override.yml")
        result = direct_commands._parse_gitops_status(out, "mysite")
        assert "Unstaged changes (1 files):" in result

    def test_ahead_of_remote(self):
        out = self._make_output(revcount="3\t0")
        result = direct_commands._parse_gitops_status(out, "mysite")
        assert "Ahead of remote by 3 commit(s)." in result

    def test_behind_remote(self):
        out = self._make_output(revcount="0\t2")
        result = direct_commands._parse_gitops_status(out, "mysite")
        assert "Behind remote by 2 commit(s)." in result

    def test_with_hosts_yaml(self):
        hosts_yaml = "hosts:\n  - role: production\n    pull_requests: true"
        out = self._make_output(hosts_yaml=hosts_yaml)
        result = direct_commands._parse_gitops_status(out, "mysite")
        assert "Role:           production" in result
        assert "Pull requests:  True" in result

    def test_missing_host_file(self):
        out = self._make_output(hostname="MISSING")
        result = direct_commands._parse_gitops_status(out, "mysite")
        assert "Host:           unknown" in result


class TestCmdGitopsStatus:
    def test_registered(self):
        assert direct_commands.is_direct_command("gitops_status")

    def test_registered_to_correct_function(self):
        # Catches a class of bug where the @register decorator drifts
        # onto an adjacent helper function. (See the gitops_status
        # regression: the @register decorator attached to the Argo CD
        # helper instead of cmd_gitops_status, so 'canasta gitops
        # status' returned the helper's tuple and sys.exit(tuple)
        # printed the tuple repr with rc=1.)
        assert direct_commands.DIRECT_COMMANDS["gitops_status"] is (
            direct_commands.cmd_gitops_status
        )


class TestDirectCommandRegistry:
    """Module-wide invariants on the direct-command registry."""

    def test_all_handlers_are_cmd_functions(self):
        # Every @register-decorated handler must be a top-level cmd_*
        # function, not a private helper. Guards against decorator
        # drift: when you edit near a @register line it can silently
        # end up above the wrong def.
        wrong = []
        for name, fn in direct_commands.DIRECT_COMMANDS.items():
            fname = getattr(fn, "__name__", "")
            if not fname.startswith("cmd_"):
                wrong.append("%s -> %s" % (name, fname))
        assert not wrong, (
            "direct-command handlers must be cmd_* functions: %s"
            % ", ".join(wrong)
        )

    def test_remote_uses_ssh(self, monkeypatch, capsys):
        d = direct_commands._SENTINEL
        ssh_output = (
            "myhost\n" + d + "\n"
            + "MISSING\n" + d + "\n"
            + "abc1234\n" + d + "\n"
            + "abc1234\n" + d + "\n"
            + "\n" + d + "\n"
            + "\n" + d + "\n"
            + "0\t0\n"
        )
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("mysite", {
                "path": "/srv/mysite",
                "orchestrator": "compose",
                "host": "admin@remote",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, ssh_output),
        )
        args = type("Args", (), {"id": "mysite"})()
        rc = direct_commands.cmd_gitops_status(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Canasta ID:     mysite" in out
        assert "Up to date with remote." in out


class TestGitopsArgocdStatus:
    def test_kubectl_missing_returns_not_registered(self, monkeypatch):
        def fake_run(*a, **kw):
            raise OSError("kubectl not found")
        monkeypatch.setattr(subprocess, "run", fake_run)
        result = direct_commands._gitops_argocd_status("mysite")
        assert result == ("Not registered", "N/A", "never", "unknown")

    def test_no_application_returns_not_registered(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 1, "stdout": "",
                "stderr": "not found",
            })(),
        )
        result = direct_commands._gitops_argocd_status("mysite")
        assert result == ("Not registered", "N/A", "never", "unknown")

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
        sync, health, last, rev = direct_commands._gitops_argocd_status("mysite")
        assert sync == "Synced"
        assert health == "Healthy"
        assert last == "2026-04-23T10:00:00Z"
        assert rev == "abcdef1"  # truncated to 7 chars

    def test_malformed_json_returns_unknown(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "not json",
            })(),
        )
        result = direct_commands._gitops_argocd_status("mysite")
        assert result == ("Unknown", "Unknown", "never", "unknown")


class TestParseGitopsStatusK8s:
    def _make_output(self, hostname="myhost", commit="abc1234", revcount="0\t0"):
        d = direct_commands._SENTINEL
        # K8s parser only reads hostname (0), commit (2), revcount (6).
        # Fill the unread slots with empty strings for the split to align.
        return (
            hostname + "\n" + d + "\n"
            + "\n" + d + "\n"
            + commit + "\n" + d + "\n"
            + "\n" + d + "\n"
            + "\n" + d + "\n"
            + "\n" + d + "\n"
            + revcount + "\n"
        )

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
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("mysite", {
                "path": "/srv/mysite",
                "orchestrator": "kubernetes",
                "host": "admin@k8s-control",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, ssh_output),
        )
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": json.dumps(app),
            })(),
        )
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


# ---------------------------------------------------------------------------
# Extension/skin list tests
# ---------------------------------------------------------------------------

class TestExecInContainer:
    def test_compose_local(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "Cite\nVisualEditor\n",
            })(),
        )
        rc, out = direct_commands._exec_in_container(
            "test",
            {"path": "/srv/test", "orchestrator": "compose"},
            "find extensions",
        )
        assert rc == 0
        assert "Cite" in out

    def test_k8s(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_k8s_get_pod",
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
        monkeypatch.setattr(
            direct_commands, "_k8s_get_pod",
            lambda ns, svc: None,
        )
        rc, out = direct_commands._exec_in_container(
            "test",
            {"path": "/srv/test", "orchestrator": "kubernetes"},
            "find extensions",
        )
        assert rc == 1


class TestExtensionSkinList:
    def test_extension_list_registered(self):
        assert direct_commands.is_direct_command("extension_list")

    def test_skin_list_registered(self):
        assert direct_commands.is_direct_command("skin_list")

    def test_extension_list_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {"path": "/srv/test", "orchestrator": "compose"}),
        )
        monkeypatch.setattr(
            direct_commands, "_exec_in_container",
            lambda *a, **kw: (0, "Cite\nVisualEditor\nParserFunctions\n"),
        )
        args = type("Args", (), {"id": "test", "wiki": None})()
        rc = direct_commands.cmd_extension_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Available Canasta extensions:" in out
        assert "Cite" in out
        assert "VisualEditor" in out

    def test_skin_list_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {"path": "/srv/test", "orchestrator": "compose"}),
        )
        monkeypatch.setattr(
            direct_commands, "_exec_in_container",
            lambda *a, **kw: (0, "Vector\nTimeless\n"),
        )
        args = type("Args", (), {"id": "test", "wiki": None})()
        rc = direct_commands.cmd_skin_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Available Canasta skins:" in out
        assert "Vector" in out

    def test_list_error(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {"path": "/srv/test", "orchestrator": "compose"}),
        )
        monkeypatch.setattr(
            direct_commands, "_exec_in_container",
            lambda *a, **kw: (1, ""),
        )
        args = type("Args", (), {"id": "test", "wiki": None})()
        rc = direct_commands.cmd_extension_list(args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Gitops diff tests
# ---------------------------------------------------------------------------

class TestParseGitopsDiff:
    def _make_output(self, uncommitted="", local="", remote="", submodules=""):
        d = direct_commands._SENTINEL
        return (
            uncommitted + "\n" + d + "\n"
            + local + "\n" + d + "\n"
            + remote + "\n" + d + "\n"
            + submodules + "\n"
        )

    def test_no_changes(self):
        out = self._make_output()
        result = direct_commands._parse_gitops_diff(out)
        assert "Uncommitted changes: 0 file(s)" in result
        assert "Local changes (not yet pushed): 0 file(s)" in result
        assert "Remote changes (would be applied on pull): 0 file(s)" in result

    def test_uncommitted_files(self):
        out = self._make_output(uncommitted="config/.env\nconfig/wikis.yaml")
        result = direct_commands._parse_gitops_diff(out)
        assert "Uncommitted changes: 2 file(s)" in result
        assert "config/.env" in result

    def test_local_and_remote(self):
        out = self._make_output(local="config/.env", remote="config/settings.php")
        result = direct_commands._parse_gitops_diff(out)
        assert "Local changes (not yet pushed): 1 file(s)" in result
        assert "Remote changes (would be applied on pull): 1 file(s)" in result

    def test_restart_hint(self):
        out = self._make_output(local="config/.env")
        result = direct_commands._parse_gitops_diff(out)
        assert "restart would be needed" in result

    def test_update_hint(self):
        out = self._make_output(remote="config/settings/global/Custom.php")
        result = direct_commands._parse_gitops_diff(out)
        assert "maintenance update may be needed" in result

    def test_submodule_changes(self):
        out = self._make_output(
            submodules="+abc123 user-extensions/MyExt (heads/main)\n def456 user-skins/MySkin"
        )
        result = direct_commands._parse_gitops_diff(out)
        assert "Submodules that would be updated:" in result
        assert "user-extensions/MyExt" in result
        assert "user-skins/MySkin" not in result


class TestCmdGitopsDiff:
    def test_registered(self):
        assert direct_commands.is_direct_command("gitops_diff")

    def test_k8s_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("k8s", {"path": "/p", "orchestrator": "kubernetes"}),
        )
        args = type("Args", (), {"id": "k8s"})()
        assert direct_commands.cmd_gitops_diff(args) is direct_commands.FALLBACK

    def test_compose_remote(self, monkeypatch, capsys):
        d = direct_commands._SENTINEL
        ssh_output = (
            "config/.env\n" + d + "\n"
            + "\n" + d + "\n"
            + "\n" + d + "\n"
            + "\n"
        )
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("mysite", {
                "path": "/srv/mysite",
                "orchestrator": "compose",
                "host": "admin@remote",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, ssh_output),
        )
        args = type("Args", (), {"id": "mysite"})()
        rc = direct_commands.cmd_gitops_diff(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Uncommitted changes: 1 file(s)" in out
        assert "config/.env" in out


# ---------------------------------------------------------------------------
# Backup list tests
# ---------------------------------------------------------------------------

class TestBackupList:
    def test_registered(self):
        assert direct_commands.is_direct_command("backup_list")

    def test_k8s_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("k8s", {"path": "/p", "orchestrator": "kubernetes"}),
        )
        args = type("Args", (), {"id": "k8s"})()
        assert direct_commands.cmd_backup_list(args) is direct_commands.FALLBACK

    def test_compose_runs_restic(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_read_env_file",
            lambda *a: {"RESTIC_REPOSITORY": "s3:s3.amazonaws.com/bucket"},
        )
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0,
                "stdout": "ID        Time                 Host\nabc123    2026-04-18 12:00:00  test\n",
            })(),
        )
        args = type("Args", (), {"id": "test"})()
        rc = direct_commands.cmd_backup_list(args)
        assert rc == 0
        assert "abc123" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Doctor tests
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_registered(self):
        assert direct_commands.is_direct_command("doctor")

    def test_parse_doctor_all_ok(self):
        d = direct_commands._SENTINEL
        parts = [
            "Python 3.12.0",
            "Docker version 27.0.0",
            "Docker Compose version v2.30.0",
            "OK",
            "user docker www-data",
            "OK",
            "v3.15.0",
            "k3s version v1.30.0",
            "REACHABLE",
            "INSTALLED",
            "git version 2.45.0",
            "OK",
            "OK",
            "Linux",
            "16 GB",
            "50G",
        ]
        stdout = ("\n" + d + "\n").join(parts) + "\n"
        result = direct_commands._parse_doctor(stdout, "myhost")
        assert "Canasta Dependency Check (myhost)" in result
        assert "Python 3:        OK" in result
        assert "Docker:          OK" in result
        assert "Docker daemon:   OK (running)" in result
        assert "kubectl:         OK" in result
        assert "crontab:         OK" in result
        assert "www-data group:  OK (member)" in result

    def test_parse_doctor_missing_deps(self):
        d = direct_commands._SENTINEL
        parts = [
            "MISSING", "MISSING", "MISSING", "NOT_RUNNING",
            "user", "MISSING", "MISSING", "MISSING",
            "UNREACHABLE", "MISSING", "MISSING", "MISSING",
            "MISSING", "Linux",
            "unknown", "unknown",
        ]
        stdout = ("\n" + d + "\n").join(parts) + "\n"
        result = direct_commands._parse_doctor(stdout, "myhost")
        assert "Python 3:        MISSING" in result
        assert "Docker:          MISSING" in result
        assert "Docker daemon:   NOT RUNNING" in result
        assert "crontab:         not installed" in result
        assert "www-data group:  NOT A MEMBER" in result

    def test_parse_doctor_macos_skips_www_data(self):
        d = direct_commands._SENTINEL
        parts = [
            "Python 3.12.0",
            "Docker version 27.0.0",
            "Docker Compose version v2.30.0",
            "OK",
            "user staff",
            "OK", "v3.15.0", "k3s version v1.30.0",
            "REACHABLE", "INSTALLED",
            "git version 2.45.0", "OK",
            "OK",
            "Darwin",
            "16 GB", "50G",
        ]
        stdout = ("\n" + d + "\n").join(parts) + "\n"
        result = direct_commands._parse_doctor(stdout, "myhost")
        assert "www-data group:  N/A (Docker Desktop handles UID mapping on Darwin)" in result
        assert "NOT A MEMBER" not in result


class TestCanastaStatus:
    """canasta status --id X — direct_only command that gathers
    pods/PVCs/ingress/certs (K8s) or `docker compose ps` (Compose)
    for a single instance."""

    def _args(self, **kw):
        from argparse import Namespace
        defaults = {"id": None, "verbose": False}
        defaults.update(kw)
        return Namespace(**defaults)

    def test_unknown_id_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(direct_commands, "_read_registry", lambda p: {})
        rc = direct_commands.cmd_status(self._args(id="ghost"))
        assert rc == 1
        assert "not found in the registry" in capsys.readouterr().err

    def test_k8s_routes_through_ssh(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_read_registry",
            lambda p: {
                "mysite": {
                    "id": "mysite", "orchestrator": "kubernetes",
                    "host": "node1", "path": "/home/admin/canasta-instances/mysite",
                },
            },
        )
        monkeypatch.setattr(
            direct_commands, "_check_running_k8s",
            lambda inst, host: True,
        )
        ssh_calls = []

        def fake_ssh(host, cmd):
            ssh_calls.append((host, cmd))
            return 0, "stub-output"

        monkeypatch.setattr(direct_commands, "_ssh_run", fake_ssh)
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

    def test_compose_uses_docker_compose_ps_locally(self, monkeypatch, capsys, tmp_path):
        path = str(tmp_path)
        monkeypatch.setattr(
            direct_commands, "_read_registry",
            lambda p: {
                "mysite": {
                    "id": "mysite", "orchestrator": "compose",
                    "host": "localhost", "path": path,
                },
            },
        )
        monkeypatch.setattr(
            direct_commands, "_check_running_compose",
            lambda p, h: False,
        )
        captured = {}

        def fake_run(cmd, cwd=None, **kw):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            return type("R", (), {"returncode": 0, "stdout": "NAME STATUS\nweb_1 Up\n"})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc = direct_commands.cmd_status(self._args(id="mysite"))
        assert rc == 0
        assert captured["cmd"][:3] == ["docker", "compose", "ps"]
        assert captured["cwd"] == path
        out = capsys.readouterr().out
        assert "STOPPED" in out
        assert "web_1 Up" in out

    def test_resolves_by_cwd_when_no_id(self, monkeypatch, tmp_path, capsys):
        path = str(tmp_path)
        monkeypatch.setattr(
            direct_commands, "_read_registry",
            lambda p: {
                "by-cwd": {
                    "id": "by-cwd", "orchestrator": "compose",
                    "host": "localhost", "path": path,
                },
            },
        )
        monkeypatch.setattr(direct_commands, "_check_running_compose", lambda p, h: True)
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": ""})(),
        )
        monkeypatch.chdir(path)
        rc = direct_commands.cmd_status(self._args(id=None))
        assert rc == 0
        assert "Instance:     by-cwd" in capsys.readouterr().out


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
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, "secret123\n"),
        )
        rc = direct_commands.cmd_argocd_password(self._args(host="node1"))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "secret123"

    def test_password_secret_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
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
        monkeypatch.setattr(
            direct_commands, "_ssh_run",
            lambda host, cmd: (0, sample),
        )
        rc = direct_commands.cmd_argocd_apps(self._args(host="node1"))
        assert rc == 0
        assert "canasta-mysite" in capsys.readouterr().out

    def test_ui_remote_invokes_ssh_tunnel(self, monkeypatch):
        """`canasta argocd ui --host node1` must run `ssh -L … kubectl
        port-forward …`, not a local kubectl call."""
        # Suppress the up-front password fetch.
        monkeypatch.setattr(
            direct_commands, "_argocd_admin_password",
            lambda host: (0, "shh"),
        )
        # Stub host resolution to a fixed user@host string.
        monkeypatch.setattr(
            direct_commands, "_resolve_ssh_target",
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
        monkeypatch.setattr(
            direct_commands, "_argocd_admin_password",
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


# ---------------------------------------------------------------------------
# canasta maintenance script / extension / update
# ---------------------------------------------------------------------------

class TestNormalizeScriptArgs:
    def test_none(self):
        args = type("Args", (), {})()
        assert direct_commands._normalize_script_args(args) == ""

    def test_empty_string(self):
        args = type("Args", (), {"script_args": ""})()
        assert direct_commands._normalize_script_args(args) == ""

    def test_string_with_whitespace(self):
        args = type("Args", (), {"script_args": "  rebuildall.php  "})()
        assert direct_commands._normalize_script_args(args) == "rebuildall.php"

    def test_list_joined(self):
        args = type("Args", (), {"script_args": ["foo.php", "--arg", "x"]})()
        assert (
            direct_commands._normalize_script_args(args)
            == "foo.php --arg x"
        )

    def test_empty_list(self):
        args = type("Args", (), {"script_args": []})()
        assert direct_commands._normalize_script_args(args) == ""


class TestMaintPathRegex:
    def test_accepts_simple(self):
        assert direct_commands._MAINT_PATH_RE.match("rebuildall.php")

    def test_accepts_with_args(self):
        assert direct_commands._MAINT_PATH_RE.match("rebuildall.php --quick")

    def test_accepts_subdir(self):
        assert direct_commands._MAINT_PATH_RE.match("Cite/maintenance/foo.php")

    def test_rejects_shell_metachar(self):
        assert not direct_commands._MAINT_PATH_RE.match("foo;rm -rf /")
        assert not direct_commands._MAINT_PATH_RE.match("foo|cat")
        assert not direct_commands._MAINT_PATH_RE.match("foo`bar`")
        assert not direct_commands._MAINT_PATH_RE.match("$(whoami)")


class TestMaintenanceScript:
    def _args(self, **kw):
        defaults = {"id": "test", "wiki": None, "script_args": None}
        defaults.update(kw)
        return type("Args", (), defaults)()

    def _patch_resolve(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
                "host": "localhost",
            }),
        )

    def test_registered(self):
        assert direct_commands.is_direct_command("maintenance_script")

    def test_no_args_lists_scripts(self, monkeypatch):
        self._patch_resolve(monkeypatch)
        captured = {}

        def fake_stream(inst_id, inst, command, service="web"):
            captured["command"] = command
            return 0

        monkeypatch.setattr(direct_commands, "_stream_in_container", fake_stream)
        rc = direct_commands.cmd_maintenance_script(self._args())
        assert rc == 0
        assert "ls maintenance/*.php" in captured["command"]
        assert "sort" in captured["command"]

    def test_invalid_path_rejected(self, monkeypatch, capsys):
        self._patch_resolve(monkeypatch)
        monkeypatch.setattr(
            direct_commands, "_stream_in_container",
            lambda *a, **kw: 0,
        )
        rc = direct_commands.cmd_maintenance_script(
            self._args(script_args="foo;rm -rf /"),
        )
        assert rc == 1
        assert "Invalid script path" in capsys.readouterr().err

    def test_runs_named_script(self, monkeypatch):
        self._patch_resolve(monkeypatch)
        captured = {}

        def fake_stream(inst_id, inst, command, service="web"):
            captured["command"] = command
            return 0

        monkeypatch.setattr(direct_commands, "_stream_in_container", fake_stream)
        rc = direct_commands.cmd_maintenance_script(
            self._args(script_args="rebuildall.php"),
        )
        assert rc == 0
        assert "php maintenance/rebuildall.php" in captured["command"]

    def test_wiki_flag_appended(self, monkeypatch):
        self._patch_resolve(monkeypatch)
        captured = {}

        def fake_stream(inst_id, inst, command, service="web"):
            captured["command"] = command
            return 0

        monkeypatch.setattr(direct_commands, "_stream_in_container", fake_stream)
        direct_commands.cmd_maintenance_script(
            self._args(script_args="rebuildall.php", wiki="main"),
        )
        assert "--wiki='main'" in captured["command"]


class TestMaintenanceExtension:
    def _args(self, **kw):
        defaults = {"id": "test", "wiki": None, "script_args": None}
        defaults.update(kw)
        return type("Args", (), defaults)()

    def _patch_resolve(self, monkeypatch):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
                "host": "localhost",
            }),
        )

    def test_registered(self):
        assert direct_commands.is_direct_command("maintenance_extension")

    def test_no_args_lists_extensions(self, monkeypatch):
        self._patch_resolve(monkeypatch)
        captured = {}

        def fake_stream(inst_id, inst, command, service="web"):
            captured["command"] = command
            return 0

        monkeypatch.setattr(direct_commands, "_stream_in_container", fake_stream)
        rc = direct_commands.cmd_maintenance_extension(self._args())
        assert rc == 0
        assert "find -L extensions" in captured["command"]
        assert "-name maintenance" in captured["command"]

    def test_runs_named_extension_script(self, monkeypatch):
        self._patch_resolve(monkeypatch)
        captured = {}

        def fake_stream(inst_id, inst, command, service="web"):
            captured["command"] = command
            return 0

        monkeypatch.setattr(direct_commands, "_stream_in_container", fake_stream)
        direct_commands.cmd_maintenance_extension(
            self._args(script_args="Cite/maintenance/foo.php"),
        )
        assert (
            "php extensions/Cite/maintenance/foo.php" in captured["command"]
        )


class TestMaintenanceUpdate:
    def _args(self, **kw):
        defaults = {
            "id": "test",
            "wiki": None,
            "skip_jobs": False,
            "skip_smw": False,
        }
        defaults.update(kw)
        return type("Args", (), defaults)()

    def _patch_resolve(self, monkeypatch, wikis=None):
        monkeypatch.setattr(
            direct_commands, "_resolve_instance",
            lambda args: ("test", {
                "path": "/srv/test",
                "orchestrator": "compose",
                "host": "localhost",
            }),
        )
        monkeypatch.setattr(
            direct_commands, "_read_wiki_ids",
            lambda inst: wikis if wikis is not None else ["main", "draft"],
        )

    def test_registered(self):
        assert direct_commands.is_direct_command("maintenance_update")

    def test_runs_update_runjobs_for_each_wiki(self, monkeypatch):
        self._patch_resolve(monkeypatch)
        # No SMW present.
        monkeypatch.setattr(
            direct_commands, "_exec_in_container",
            lambda *a, **kw: (0, "no\n"),
        )
        commands = []

        def fake_stream(inst_id, inst, command, service="web"):
            commands.append(command)
            return 0

        monkeypatch.setattr(direct_commands, "_stream_in_container", fake_stream)
        rc = direct_commands.cmd_maintenance_update(self._args())
        assert rc == 0
        # update.php for both wikis, then runJobs for both wikis.
        assert any(
            "update.php --wiki='main'" in c for c in commands
        )
        assert any(
            "update.php --wiki='draft'" in c for c in commands
        )
        assert any(
            "runJobs.php --wiki='main'" in c for c in commands
        )

    def test_skip_jobs_skips_runjobs(self, monkeypatch):
        self._patch_resolve(monkeypatch, wikis=["main"])
        monkeypatch.setattr(
            direct_commands, "_exec_in_container",
            lambda *a, **kw: (0, "no\n"),
        )
        commands = []
        monkeypatch.setattr(
            direct_commands, "_stream_in_container",
            lambda iid, i, c, service="web": commands.append(c) or 0,
        )
        direct_commands.cmd_maintenance_update(self._args(skip_jobs=True))
        assert any("update.php" in c for c in commands)
        assert not any("runJobs.php" in c for c in commands)

    def test_smw_runs_when_present(self, monkeypatch):
        self._patch_resolve(monkeypatch, wikis=["main"])
        monkeypatch.setattr(
            direct_commands, "_exec_in_container",
            lambda *a, **kw: (0, "yes\n"),
        )
        commands = []
        monkeypatch.setattr(
            direct_commands, "_stream_in_container",
            lambda iid, i, c, service="web": commands.append(c) or 0,
        )
        direct_commands.cmd_maintenance_update(self._args())
        assert any("rebuildData.php" in c for c in commands)

    def test_skip_smw_skips_rebuilddata(self, monkeypatch):
        self._patch_resolve(monkeypatch, wikis=["main"])
        # _exec_in_container shouldn't even be called when skip_smw is True;
        # still stub it defensively.
        monkeypatch.setattr(
            direct_commands, "_exec_in_container",
            lambda *a, **kw: (0, "yes\n"),
        )
        commands = []
        monkeypatch.setattr(
            direct_commands, "_stream_in_container",
            lambda iid, i, c, service="web": commands.append(c) or 0,
        )
        direct_commands.cmd_maintenance_update(self._args(skip_smw=True))
        assert not any("rebuildData.php" in c for c in commands)

    def test_explicit_wiki_overrides_wikis_yaml(self, monkeypatch):
        self._patch_resolve(monkeypatch, wikis=["main", "draft", "foo"])
        monkeypatch.setattr(
            direct_commands, "_exec_in_container",
            lambda *a, **kw: (0, "no\n"),
        )
        commands = []
        monkeypatch.setattr(
            direct_commands, "_stream_in_container",
            lambda iid, i, c, service="web": commands.append(c) or 0,
        )
        direct_commands.cmd_maintenance_update(self._args(wiki="draft"))
        # Only the named wiki should appear in the commands.
        assert any("--wiki='draft'" in c for c in commands)
        assert not any("--wiki='main'" in c for c in commands)
        assert not any("--wiki='foo'" in c for c in commands)
