"""Tests for handing operator-managed directories back on rootless Podman.

The web container's startup rsync re-owns config/, extensions/ and skins/ to
a host subuid on every start, so both the Ansible task and the Compose fast
path must re-apply the chown after the containers come up.
"""

import os
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers

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
