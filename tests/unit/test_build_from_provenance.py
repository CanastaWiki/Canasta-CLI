"""A build-from image is built without BuildKit's provenance attestation.

The attestation differs on every build, so a rebuild of unchanged source
produced a new image ID and registry digest, and upgrade restarted the
instance every time. The flag goes only to builders that accept it: the
legacy Docker builder and podman reject it.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD_FROM = os.path.join(
    REPO_ROOT, "roles", "imagebuild", "tasks", "build_from_source.yml")

STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$STUB_LOG"
if [ "$1" = buildx ]; then exit "${STUB_BUILDX_RC:-0}"; fi
exit 0
"""


def _build(tmp_path, runtime, env_extra=None, build_arg=None):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("docker", "podman"):
        (bindir / name).write_text(STUB)
        (bindir / name).chmod(0o755)
    src = tmp_path / "src"
    for repo in ("Canasta", "CanastaBase"):
        (src / repo).mkdir(parents=True)
        (src / repo / "Dockerfile").write_text("FROM scratch\n")
    log = tmp_path / "calls.log"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"inspect_command": runtime, "build_from": str(src),
                 "build_arg": build_arg or [],
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": BUILD_FROM}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               STUB_LOG=str(log))
    env.pop("DOCKER_BUILDKIT", None)
    env.update(env_extra or {})
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    builds = [line for line in log.read_text().splitlines()
              if line.split()[1] == "build"]
    assert len(builds) == 2, builds
    return builds


def test_buildkit_builds_drop_the_attestation(tmp_path):
    for line in _build(tmp_path, "docker"):
        assert "--provenance=false" in line.split()


def test_user_build_args_still_follow(tmp_path):
    builds = _build(tmp_path, "docker", build_arg=["FOO=bar"])
    for line in builds:
        args = line.split()
        assert args.index("--provenance=false") < args.index("FOO=bar")


@pytest.mark.parametrize("runtime, env_extra", [
    ("podman", {}),
    ("docker", {"STUB_BUILDX_RC": "1"}),
    ("docker", {"DOCKER_BUILDKIT": "0"}),
])
def test_other_builders_do_not_get_the_flag(tmp_path, runtime, env_extra):
    for line in _build(tmp_path, runtime, env_extra):
        assert line.startswith(runtime + " build")
        assert "--provenance" not in line
