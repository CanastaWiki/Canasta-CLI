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
