"""Tests for `canasta rebuild` (#562)."""

import json
import os
import sys
import types


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
            lambda inst, include_sidecars=False: [],
        )
        rc = direct_commands.cmd_rebuild(_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "Nothing to rebuild" in out or "nothing to rebuild" in out


class TestRebuildBuildAndRestart:
    def test_default_flow_builds_then_restarts(self, monkeypatch):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst, include_sidecars=False: ["web"],
        )
        calls = []

        def fake_run_compose(inst_id, inst, action_args, include_sidecars=False):
            calls.append(list(action_args))
            return 0

        monkeypatch.setattr(direct_commands._helpers, "_run_compose", fake_run_compose)
        monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
            lambda inst: None,
        )
        monkeypatch.setattr(direct_commands._helpers, "_wait_web_ready",
            lambda i, inst: 0,
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
            lambda inst, include_sidecars=False: ["web"],
        )
        calls = []
        monkeypatch.setattr(direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, action_args, include_sidecars=False:
                (calls.append(list(action_args)) or 0),
        )
        monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
            lambda inst: None,
        )

        direct_commands.cmd_rebuild(_args(no_cache=True))
        assert calls[0] == ["build", "--no-cache", "web"]

    def test_multiple_buildable_services_all_built(self, monkeypatch):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst, include_sidecars=False: ["web", "varnish-custom"],
        )
        calls = []
        monkeypatch.setattr(direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, action_args, include_sidecars=False:
                (calls.append(list(action_args)) or 0),
        )
        monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
            lambda inst: None,
        )

        direct_commands.cmd_rebuild(_args())
        assert calls[0] == ["build", "web", "varnish-custom"]

    def test_no_restart_skips_down_up(self, monkeypatch, capsys):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst, include_sidecars=False: ["web"],
        )
        calls = []
        monkeypatch.setattr(direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, action_args, include_sidecars=False:
                (calls.append(list(action_args)) or 0),
        )

        rc = direct_commands.cmd_rebuild(_args(no_restart=True))
        assert rc == 0
        assert calls == [["build", "web"]]
        out = capsys.readouterr().out
        assert "--no-restart" in out
        assert "canasta restart" in out

    def test_waits_for_web_after_the_restart(self, monkeypatch):
        # A rebuild replaces the image, so the restart it drives is the case
        # where composer runs longest — returning before web is ready is
        # exactly when a follow-up command races it.
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst, include_sidecars=False: ["web"],
        )
        events = []
        monkeypatch.setattr(direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, action, include_sidecars=False:
                events.append(action[0]) or 0,
        )
        monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
            lambda inst: None,
        )
        monkeypatch.setattr(direct_commands._helpers, "_wait_web_ready",
            lambda i, inst: events.append("wait") or 1,
        )
        assert direct_commands.cmd_rebuild(_args()) == 1
        assert events == ["build", "down", "up", "wait"]

    def test_no_restart_does_not_wait(self, monkeypatch):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst, include_sidecars=False: ["web"],
        )
        events = []
        monkeypatch.setattr(direct_commands._helpers, "_run_compose",
            lambda inst_id, inst, action, include_sidecars=False:
                events.append(action[0]) or 0,
        )
        monkeypatch.setattr(direct_commands._helpers, "_wait_web_ready",
            lambda i, inst: events.append("wait") or 0,
        )
        assert direct_commands.cmd_rebuild(_args(no_restart=True)) == 0
        assert events == ["build"]

    def test_build_failure_skips_restart_and_returns_code(self, monkeypatch):
        _patch_compose_inst(monkeypatch)
        monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
            lambda inst, include_sidecars=False: ["web"],
        )
        calls = []

        def fake_run_compose(inst_id, inst, action_args, include_sidecars=False):
            calls.append(list(action_args))
            return 2 if action_args[0] == "build" else 0

        monkeypatch.setattr(direct_commands._helpers, "_run_compose", fake_run_compose)
        rc = direct_commands.cmd_rebuild(_args())
        assert rc == 2
        # Only the build call was attempted, no down/up
        assert calls == [["build", "web"]]


class TestRebuildSidecars:
    """rebuild on a sidecar instance must layer docker-compose.sidecars.yml
    into every compose call (as the Ansible stop/start path does), or the
    down/up cycle runs with an incomplete file set and drops the sidecars."""

    def _sidecar_inst(self, tmp_path, sidecars_yaml):
        site = tmp_path / "test"
        (site / "config").mkdir(parents=True)
        (site / "config" / "sidecars.yaml").write_text(sidecars_yaml)
        return site

    def _run(self, monkeypatch, site):
        monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
            lambda args: ("test", {
                "path": str(site),
                "orchestrator": "compose",
                "host": "localhost",
                "devMode": False,
            }),
        )
        listed = {}

        def fake_list(inst, include_sidecars=False):
            listed["include_sidecars"] = include_sidecars
            return ["web"]

        monkeypatch.setattr(
            direct_commands.rebuild, "_list_buildable_services", fake_list)
        calls = []

        def fake_run_compose(inst_id, inst, action_args, include_sidecars=False):
            calls.append((list(action_args), include_sidecars))
            return 0

        monkeypatch.setattr(
            direct_commands._helpers, "_run_compose", fake_run_compose)
        monkeypatch.setattr(
            direct_commands._helpers, "_sync_compose_profiles", lambda inst: None)
        monkeypatch.setattr(
            direct_commands._helpers, "_wait_web_ready", lambda i, inst: 0)
        rc = direct_commands.cmd_rebuild(_args())
        return rc, listed, calls

    def test_sidecar_instance_layers_sidecars_in_all_compose_calls(
            self, monkeypatch, tmp_path):
        site = self._sidecar_inst(
            tmp_path, "sidecars:\n  - name: cache\n    image: redis:7\n")
        rc, listed, calls = self._run(monkeypatch, site)
        assert rc == 0
        assert listed["include_sidecars"] is True
        assert calls == [
            (["build", "web"], True),
            (["down"], True),
            (["up", "-d"], True),
        ]

    def test_no_sidecars_keeps_layer_off(self, monkeypatch, tmp_path):
        # An empty sidecars list must NOT pull in a lingering rendered
        # file: after `sidecar remove` the layer would recreate the
        # removed sidecar on `up -d`.
        site = self._sidecar_inst(tmp_path, "sidecars: []\n")
        rc, listed, calls = self._run(monkeypatch, site)
        assert rc == 0
        assert listed["include_sidecars"] is False
        assert all(flag is False for _args_, flag in calls)


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

    def test_returns_none_on_unparseable_output(self, monkeypatch):
        def fake_subprocess_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = "\t- not: [valid"
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
        # None, not []: a failed render must stay distinguishable from an
        # instance that genuinely has no buildable service.
        assert result is None

    def test_returns_none_on_compose_config_failure(self, monkeypatch, capsys):
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
        # None, not []: a failed render must stay distinguishable from an
        # instance that genuinely has no buildable service.
        assert result is None
        err = capsys.readouterr().err
        assert "compose config failed" in err


class TestListBuildableServicesOnPodman:
    """The enumeration has to work on podman-compose, not just Docker.

    `--format json` is Docker Compose v2 only — podman-compose has no
    such option and exits 2 — and podman-compose ignores
    COMPOSE_PROFILES from .env. Either one alone produced an empty
    service list, which `canasta rebuild` reported as "nothing to
    rebuild" while exiting 0.
    """

    def _capture(self, monkeypatch, podman, profiles=None):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd

            class R:
                returncode = 0
                stdout = (
                    "services:\n"
                    "  web:\n"
                    "    build:\n"
                    "      context: .\n"
                    "  db:\n"
                    "    image: mariadb:11.4\n"
                )
                stderr = ""
            return R()

        monkeypatch.setattr(direct_commands._helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(
            direct_commands._helpers, "_compose_file_args", lambda *a, **kw: [])
        monkeypatch.setattr(
            direct_commands._helpers, "_is_podman_compose", lambda i: podman)
        monkeypatch.setattr(
            direct_commands._helpers, "_compose_profile_args",
            lambda i: list(profiles or []))
        monkeypatch.setattr(direct_commands.rebuild.subprocess, "run", fake_run)

        result = direct_commands._list_buildable_services(
            {"path": "/srv/test", "host": "localhost", "devMode": False})
        return result, seen["cmd"]

    def test_podman_gets_no_format_flag(self, monkeypatch):
        result, cmd = self._capture(monkeypatch, podman=True)
        assert "--format" not in cmd, (
            "podman-compose has no --format option and exits 2 on it"
        )
        assert result == ["web"], (
            "podman-compose renders YAML; the parser must read it"
        )

    def test_docker_keeps_the_format_flag(self, monkeypatch):
        _, cmd = self._capture(monkeypatch, podman=False)
        assert "--format" in cmd and "json" in cmd

    def test_podman_profiles_are_passed_explicitly(self, monkeypatch):
        _, cmd = self._capture(
            monkeypatch, podman=True, profiles=["--profile", "internal-db"])
        assert "--profile" in cmd and "internal-db" in cmd, (
            "podman-compose ignores COMPOSE_PROFILES from .env, so profiled "
            "services vanish from the rendered config without these flags"
        )


class TestRebuildFailsLoudlyWhenEnumerationBreaks:
    def test_enumeration_failure_is_not_reported_as_nothing_to_rebuild(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            direct_commands.rebuild, "_list_buildable_services",
            lambda *a, **kw: None)
        monkeypatch.setattr(
            direct_commands._helpers, "_resolve_instance",
            lambda a: ("mysite", {"path": "/srv/mysite", "orchestrator": "compose"}))
        monkeypatch.setattr(
            direct_commands._helpers, "_instance_has_sidecars", lambda i: False)

        rc = direct_commands.rebuild.cmd_rebuild(
            types.SimpleNamespace(id="mysite", no_cache=False, no_restart=False))
        assert rc == 1, "a failed enumeration must not exit 0"
        assert "nothing was rebuilt" in capsys.readouterr().err
