"""Tests for `canasta rebuild` (#562)."""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402


def _args(**kw):
    defaults = {"id": "test", "no_cache": False, "no_restart": False}
    defaults.update(kw)
    return type("Args", (), defaults)()


def _patch_compose_inst(monkeypatch):
    """Stub _resolve_instance to return a Compose instance."""
    monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
        lambda args: ("test", {
            "path": "/srv/test",
            "orchestrator": "compose",
            "host": "localhost",
            "devMode": False,
        }),
    )


def _patch_k8s_inst(monkeypatch):
    monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
        lambda args: ("test", {
            "path": "/srv/test",
            "orchestrator": "k8s",
            "host": "localhost",
            "devMode": False,
        }),
    )


class TestRebuildRegistered:
    def test_registered_as_direct(self):
        assert direct_commands.is_direct_command("rebuild")


class TestRebuildK8sRefused:
    def test_k8s_instance_rejects_with_error(self, monkeypatch, capsys):
        _patch_k8s_inst(monkeypatch)
        rc = direct_commands.cmd_rebuild(_args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "Compose" in err
        assert "Kubernetes" in err


class TestRebuildNoBuildableServices:
    def test_returns_zero_with_message(self, monkeypatch, capsys):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst: [],
        )
        rc = direct_commands.cmd_rebuild(_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "Nothing to rebuild" in out or "nothing to rebuild" in out


class TestRebuildBuildAndRestart:
    def test_default_flow_builds_then_restarts(self, monkeypatch):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst: ["web"],
        )
        calls = []

        def fake_run_compose(inst_id, inst, action_args):
            calls.append(list(action_args))
            return 0

        monkeypatch.setattr(direct_commands._helpers, "_run_compose", fake_run_compose)
        monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
            lambda inst: None,
        )

        rc = direct_commands.cmd_rebuild(_args())
        assert rc == 0
        # Expect: build web, down, up -d
        assert calls[0] == ["build", "web"]
        assert calls[1] == ["down"]
        assert calls[2] == ["up", "-d"]
        assert len(calls) == 3

    def test_no_cache_flag_passes_through(self, monkeypatch):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst: ["web"],
        )
        calls = []
        monkeypatch.setattr(direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, action_args: (calls.append(list(action_args)) or 0),
        )
        monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
            lambda inst: None,
        )

        direct_commands.cmd_rebuild(_args(no_cache=True))
        assert calls[0] == ["build", "--no-cache", "web"]

    def test_multiple_buildable_services_all_built(self, monkeypatch):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst: ["web", "varnish-custom"],
        )
        calls = []
        monkeypatch.setattr(direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, action_args: (calls.append(list(action_args)) or 0),
        )
        monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
            lambda inst: None,
        )

        direct_commands.cmd_rebuild(_args())
        assert calls[0] == ["build", "web", "varnish-custom"]

    def test_no_restart_skips_down_up(self, monkeypatch, capsys):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst: ["web"],
        )
        calls = []
        monkeypatch.setattr(direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, action_args: (calls.append(list(action_args)) or 0),
        )

        rc = direct_commands.cmd_rebuild(_args(no_restart=True))
        assert rc == 0
        assert calls == [["build", "web"]]
        out = capsys.readouterr().out
        assert "--no-restart" in out
        assert "canasta restart" in out

    def test_build_failure_skips_restart_and_returns_code(self, monkeypatch):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst: ["web"],
        )
        calls = []

        def fake_run_compose(inst_id, inst, action_args):
            calls.append(list(action_args))
            return 2 if action_args[0] == "build" else 0

        monkeypatch.setattr(direct_commands._helpers, "_run_compose", fake_run_compose)
        rc = direct_commands.cmd_rebuild(_args())
        assert rc == 2
        # Only the build call was attempted, no down/up
        assert calls == [["build", "web"]]


class TestListBuildableServices:
    def test_extracts_services_with_build_directive(self, monkeypatch):
        compose_json = {
            "services": {
                "web": {"build": {"context": ".", "dockerfile": "Dockerfile.custom"}},
                "db": {"image": "mariadb:11.4"},
                "varnish": {"image": "varnish:alpine"},
                "extra": {"build": {"context": "./other"}},
            }
        }

        def fake_subprocess_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = json.dumps(compose_json)
                stderr = ""
            return R()

        monkeypatch.setattr(direct_commands._helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(direct_commands._helpers, "_compose_file_args",
            lambda *a, **kw: ["-f", "docker-compose.yml"],
        )
        monkeypatch.setattr(direct_commands.rebuild.subprocess, "run", fake_subprocess_run)

        result = direct_commands._list_buildable_services({
            "path": "/srv/test",
            "host": "localhost",
            "devMode": False,
        })
        assert sorted(result) == ["extra", "web"]

    def test_returns_empty_on_invalid_json(self, monkeypatch):
        def fake_subprocess_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = "not json"
                stderr = ""
            return R()

        monkeypatch.setattr(direct_commands._helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(direct_commands._helpers, "_compose_file_args",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr(direct_commands.rebuild.subprocess, "run", fake_subprocess_run)

        result = direct_commands._list_buildable_services({
            "path": "/srv/test",
            "host": "localhost",
            "devMode": False,
        })
        assert result == []

    def test_returns_empty_on_compose_config_failure(self, monkeypatch, capsys):
        def fake_subprocess_run(cmd, **kw):
            class R:
                returncode = 1
                stdout = ""
                stderr = "compose error"
            return R()

        monkeypatch.setattr(direct_commands._helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(direct_commands._helpers, "_compose_file_args",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr(direct_commands.rebuild.subprocess, "run", fake_subprocess_run)

        result = direct_commands._list_buildable_services({
            "path": "/srv/test",
            "host": "localhost",
            "devMode": False,
        })
        assert result == []
        err = capsys.readouterr().err
        assert "compose config failed" in err
