"""Tests for handing operator-managed directories back on rootless Podman.

The web container's startup rsync re-owns config/, extensions/ and skins/ to
a host subuid on every start, so both the Ansible task and the Compose fast
path must re-apply the chown after the containers come up.
"""

import os
import sys

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import _helpers  # noqa: E402

TASK_FILE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "_rootless_operator_chown.yml")

DOCKER = {"path": "/srv/test", "orchestrator": "compose",
          "host": "localhost", "inspectCommand": "docker"}
PODMAN = {"path": "/srv/test", "orchestrator": "compose", "host": "localhost",
          "composeCommand": "podman-compose", "inspectCommand": "podman"}


def _runtime(monkeypatch, responses):
    """Stub _runtime_capture with a queue of (rc, stdout); return argv seen."""
    seen = []
    queue = list(responses)

    def fake(inst, argv, timeout=30):
        seen.append(argv)
        return queue.pop(0)

    monkeypatch.setattr(_helpers, "_runtime_capture", fake)
    return seen


def _load_tasks():
    with open(TASK_FILE) as f:
        return {t["name"]: t for t in yaml.safe_load(f)}


class TestHelper:
    def test_docker_is_not_probed(self, monkeypatch):
        seen = _runtime(monkeypatch, [])
        _helpers._rootless_operator_chown(dict(DOCKER))
        assert seen == []

    def test_rootful_podman_is_probed_but_not_chowned(self, monkeypatch):
        seen = _runtime(monkeypatch, [(0, "false\n")])
        _helpers._rootless_operator_chown(dict(PODMAN))
        assert len(seen) == 1
        assert seen[0][:2] == ["podman", "info"]

    def test_failed_probe_does_not_chown(self, monkeypatch):
        seen = _runtime(monkeypatch, [(125, "")])
        _helpers._rootless_operator_chown(dict(PODMAN))
        assert len(seen) == 1

    def test_rootless_chowns_each_dir_skipping_missing(self, monkeypatch):
        seen = _runtime(monkeypatch, [(0, "true\n"), (0, "")])
        _helpers._rootless_operator_chown(dict(PODMAN))
        assert len(seen) == 2
        assert seen[1][:4] == ["podman", "unshare", "sh", "-c"]
        script = seen[1][4]
        assert "[ ! -d '/srv/test/config' ] || chown 0:0 '/srv/test/config';" \
            in script
        for rel in ("config/settings", "public_assets", "extensions", "skins"):
            q = "'/srv/test/%s'" % rel
            assert "[ ! -d %s ] || chown -R 0:0 %s;" % (q, q) in script
        # Container-written runtime dirs stay container-owned.
        assert "images" not in script
        assert "persistent" not in script

    def test_chown_failure_warns_without_raising(self, monkeypatch, capsys):
        _runtime(monkeypatch, [(0, "true\n"), (1, "")])
        _helpers._rootless_operator_chown(dict(PODMAN))
        assert "Warning" in capsys.readouterr().err


class TestFastPathsChownAfterStart:
    """start, restart and rebuild bring containers up without Ansible."""

    @pytest.fixture
    def stubbed(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_resolve_instance",
                            lambda args: ("test", dict(PODMAN)))
        monkeypatch.setattr(_helpers, "_instance_has_sidecars",
                            lambda inst: False)
        monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                            lambda inst: {})
        monkeypatch.setattr(_helpers, "_check_running_compose",
                            lambda *a: False)
        monkeypatch.setattr(_helpers, "_missing_db_password",
                            lambda inst, env: False)
        monkeypatch.setattr(_helpers, "_run_compose",
                            lambda *a, **kw: 0)
        monkeypatch.setattr(direct_commands.rebuild,
                            "_list_buildable_services",
                            lambda inst, include_sidecars=False: ["web"])
        chowned = []
        monkeypatch.setattr(_helpers, "_rootless_operator_chown",
                            lambda inst: chowned.append(inst["path"]))
        return chowned

    @staticmethod
    def _args():
        return type("Args", (), {"id": "test", "no_cache": False,
                                 "no_restart": False})()

    @pytest.mark.parametrize("cmd", ["cmd_start", "cmd_rebuild"])
    def test_chowns_once_web_is_ready(self, monkeypatch, stubbed, cmd):
        monkeypatch.setattr(_helpers, "_wait_web_ready", lambda i, inst: 0)
        assert getattr(direct_commands, cmd)(self._args()) == 0
        assert stubbed == ["/srv/test"]

    @pytest.mark.parametrize("cmd", ["cmd_start", "cmd_rebuild"])
    def test_skips_chown_when_web_never_ready(self, monkeypatch, stubbed,
                                              cmd):
        monkeypatch.setattr(_helpers, "_wait_web_ready", lambda i, inst: 1)
        assert getattr(direct_commands, cmd)(self._args()) == 1
        assert stubbed == []


class TestAnsibleTask:
    def test_probe_is_gated_on_podman(self):
        probe = _load_tasks()["Detect rootless Podman (only when runtime is podman)"]
        assert any("podman" in c for c in probe["when"])

    def test_dirs_are_statted_only_when_rootless(self):
        stat = _load_tasks()["Find the operator-managed directories that exist"]
        assert any("_rootless_chown_rootless" in c for c in stat["when"])

    def test_chown_loops_only_over_existing_dirs(self):
        chown = _load_tasks()["Fix operator directory ownership for rootless Podman"]
        assert "_rootless_chown_dirs" in chown["loop"]
        assert "stat.exists" in chown["loop"]

    def test_dir_list_matches_fast_path(self):
        stat = _load_tasks()["Find the operator-managed directories that exist"]
        ansible_dirs = [
            (i["path"].replace("{{ instance_path }}/", ""), i["recurse"])
            for i in stat["loop"]
        ]
        assert ansible_dirs == list(_helpers._ROOTLESS_OPERATOR_DIRS)
