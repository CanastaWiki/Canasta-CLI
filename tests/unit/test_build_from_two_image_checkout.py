"""--build-from on a Canasta checkout that holds both images' source.

A Canasta checkout with Dockerfile.canasta builds canasta-base from its
Dockerfile and then canasta from Dockerfile.canasta on top of it. Anything
else under Canasta/ is an overlay on the Canasta release image, except a
Canasta checkout too old to hold the canasta-base source, which is refused.
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


def _build(tmp_path, files, build_arg=(), base_only=False, expect_rc=0):
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
                 "build_arg": list(build_arg),
                 "build_base_only": base_only,
                 "canasta_default_image": DEFAULT_IMAGE,
                 "id": "site",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": BUILD_FROM}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               STUB_LOG=str(log))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == expect_rc, proc.stdout + proc.stderr
    builds = [line.split() for line in log.read_text().splitlines()]
    return builds, proc.stdout + proc.stderr


def test_two_image_checkout_builds_base_then_canasta(tmp_path):
    builds, _ = _build(tmp_path, ["Canasta/Dockerfile",
                                  "Canasta/Dockerfile.canasta"])
    assert len(builds) == 2, builds
    base, canasta = builds
    assert base[0] == "Canasta"
    assert base[-3:] == ["-t", "canasta-base:local-site", "."]
    assert "-f" not in base
    assert canasta[0] == "Canasta"
    assert canasta[canasta.index("-f") + 1] == "Dockerfile.canasta"
    assert "BASE_IMAGE=canasta-base:local-site" in canasta
    assert canasta[-3:] == ["-t", "canasta:local-site", "."]


def test_canasta_checkout_without_the_base_source_is_refused(tmp_path):
    builds, out = _build(tmp_path, ["Canasta/Dockerfile",
                                    "Canasta/contents.yaml",
                                    "CanastaBase/Dockerfile"], expect_rc=2)
    assert builds == []
    assert "has no Dockerfile.canasta" in out, out


def test_overlay_builds_on_the_canasta_release(tmp_path):
    builds, _ = _build(tmp_path, ["Canasta/Dockerfile"])
    assert len(builds) == 1, builds
    assert "-f" not in builds[0]
    assert "BASE_IMAGE=" + DEFAULT_IMAGE in builds[0]


def test_user_base_image_skips_the_base_build(tmp_path):
    builds, _ = _build(tmp_path, ["Canasta/Dockerfile",
                                  "Canasta/Dockerfile.canasta"],
                       build_arg=["BASE_IMAGE=example.com/base:1"])
    assert len(builds) == 1, builds
    assert builds[0][builds[0].index("-f") + 1] == "Dockerfile.canasta"
    assert builds[0][-5:-3] == ["--build-arg", "BASE_IMAGE=example.com/base:1"]


def test_base_only_builds_canasta_base_under_the_output_tag(tmp_path):
    builds, _ = _build(tmp_path, ["Canasta/Dockerfile",
                                  "Canasta/Dockerfile.canasta"],
                       base_only=True)
    assert len(builds) == 1, builds
    assert builds[0][0] == "Canasta"
    assert "-f" not in builds[0]
    assert builds[0][-3:] == ["-t", "canasta:local-site", "."]


def test_base_only_without_base_source_fails(tmp_path):
    builds, _ = _build(tmp_path, ["Canasta/Dockerfile"], base_only=True,
                       expect_rc=2)
    assert builds == []


def test_base_only_failure_names_the_missing_source():
    with open(BUILD_FROM) as f:
        task = next(t for t in yaml.safe_load(f) if t.get("name")
                    == "Fail if there is no canasta-base source to build alone")
    assert "builds canasta-base alone" in task["ansible.builtin.fail"]["msg"]
    assert task["when"] == ["_build_base_only | bool", "_base_source_dir == ''"]
