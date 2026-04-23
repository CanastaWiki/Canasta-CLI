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


class TestCheckDirExists:
    def test_local_exists(self, tmp_path):
        d = tmp_path / "exists"
        d.mkdir()
        assert direct_commands._check_dir_exists(str(d), "localhost")

    def test_local_missing(self, tmp_path):
        assert not direct_commands._check_dir_exists(str(tmp_path / "nope"), "localhost")


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
        is always printed, even without -i/--all."""
        (tmp_path / "VERSION").write_text("4.0.0\n")
        (tmp_path / "CANASTA_VERSION").write_text("3.5.7\n")
        (tmp_path / "BUILD_COMMIT").write_text("abc1234\n")
        (tmp_path / "BUILD_DATE").write_text("2026-04-23 00:00:00\n")
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        monkeypatch.setattr(direct_commands, "_get_script_dir", lambda: str(tmp_path))
        # No instances registered — PWD auto-resolve should be a no-op.
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        args = type("Args", (), {"id": None, "all": False, "host": None})()
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
        args = type("Args", (), {"id": None, "all": False, "host": None})()
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
        assert len(ssh_cmds) == 1
        assert "up -d" in ssh_cmds[0][1]
        assert ssh_cmds[0][0] == "admin@remote"


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
        assert "www-data group:  OK (member)" in result

    def test_parse_doctor_missing_deps(self):
        d = direct_commands._SENTINEL
        parts = [
            "MISSING", "MISSING", "MISSING", "NOT_RUNNING",
            "user", "MISSING", "MISSING", "MISSING",
            "UNREACHABLE", "MISSING", "MISSING", "MISSING",
            "unknown", "unknown",
        ]
        stdout = ("\n" + d + "\n").join(parts) + "\n"
        result = direct_commands._parse_doctor(stdout, "myhost")
        assert "Python 3:        MISSING" in result
        assert "Docker:          MISSING" in result
        assert "Docker daemon:   NOT RUNNING" in result
        assert "www-data group:  NOT A MEMBER" in result
