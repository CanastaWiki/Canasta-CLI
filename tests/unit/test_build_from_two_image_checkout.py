"""--build-from on a Canasta checkout that holds both images' source.

A Canasta checkout with Dockerfile.canasta builds canasta-base from its
Dockerfile and then canasta from Dockerfile.canasta on top of it. Without
Dockerfile.canasta, the existing layouts keep working: canasta-base from a
CanastaBase/ checkout, the published canasta-base for a Canasta checkout on
its own, and the Canasta release image for an overlay.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD_FROM = os.path.join(
    REPO_ROOT, "roles", "imagebuild", "tasks", "build_from_source.yml")
DEFAULT_IMAGE = "ghcr.io/canastawiki/canasta:3.6.0"

STUB = """#!/bin/sh
if [ "$1" = build ]; then
  echo "$(basename "$(pwd)") $*" >> "$STUB_LOG"
fi
exit 0
"""


def _build(tmp_path, files):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "podman").write_text(STUB)
    (bindir / "podman").chmod(0o755)
    src = tmp_path / "src"
    for rel in files:
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("FROM scratch\n")
    log = tmp_path / "builds.log"
    log.write_text("")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"inspect_command": "podman", "build_from": str(src),
                 "build_arg": [], "canasta_default_image": DEFAULT_IMAGE,
                 "instance_is_k8s": False, "id": "site",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": BUILD_FROM}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               STUB_LOG=str(log))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    builds = [line.split() for line in log.read_text().splitlines()]
    return builds, proc.stdout


def test_two_image_checkout_builds_base_then_canasta(tmp_path):
    builds, _ = _build(tmp_path, ["Canasta/Dockerfile",
                                  "Canasta/Dockerfile.canasta"])
    assert len(builds) == 2, builds
    base, canasta = builds
    assert base[0] == "Canasta"
    assert base[-3:] == ["-t", "canasta-base:local", "."]
    assert "-f" not in base
    assert canasta[0] == "Canasta"
    assert canasta[canasta.index("-f") + 1] == "Dockerfile.canasta"
    assert "BASE_IMAGE=canasta-base:local" in canasta
    assert canasta[-3:] == ["-t", "canasta:local-site", "."]


def test_two_image_checkout_ignores_canastabase(tmp_path):
    builds, out = _build(tmp_path, ["Canasta/Dockerfile",
                                    "Canasta/Dockerfile.canasta",
                                    "CanastaBase/Dockerfile"])
    assert [b[0] for b in builds] == ["Canasta", "Canasta"]
    assert "Warning: " + str(tmp_path / "src" / "CanastaBase") in out


def test_separate_canastabase_checkout(tmp_path):
    builds, out = _build(tmp_path, ["Canasta/Dockerfile",
                                    "Canasta/contents.yaml",
                                    "CanastaBase/Dockerfile"])
    assert [b[0] for b in builds] == ["CanastaBase", "Canasta"]
    assert "-f" not in builds[1]
    assert "BASE_IMAGE=canasta-base:local" in builds[1]
    assert "Warning:" not in out


def test_canasta_checkout_alone_uses_published_base(tmp_path):
    builds, _ = _build(tmp_path, ["Canasta/Dockerfile",
                                  "Canasta/contents.yaml"])
    assert len(builds) == 1, builds
    assert "-f" not in builds[0]
    assert ("BASE_IMAGE=ghcr.io/canastawiki/canasta-base:latest"
            in builds[0])


def test_overlay_builds_on_the_canasta_release(tmp_path):
    builds, _ = _build(tmp_path, ["Canasta/Dockerfile"])
    assert len(builds) == 1, builds
    assert "-f" not in builds[0]
    assert "BASE_IMAGE=" + DEFAULT_IMAGE in builds[0]
