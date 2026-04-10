"""Tests for the canasta-docker bash wrapper.

Uses CANASTA_DOCKER_DRY_RUN=1 to make the wrapper print the assembled
docker run command (one argument per line) instead of executing it.
This lets us assert on argument parsing and conditional mounts without
needing a Docker daemon.

Note: macOS limits AF_UNIX paths to 104 bytes, so any test that creates
real unix sockets uses short paths under /tmp rather than pytest's
tmp_path fixture (which lives under /private/var/folders/...).
"""

import os
import shutil
import socket
import subprocess
import tempfile

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WRAPPER = os.path.join(REPO_ROOT, "canasta-docker")


@pytest.fixture
def short_tmp():
    """A short /tmp-based temporary directory (for AF_UNIX path limits)."""
    d = tempfile.mkdtemp(prefix="cd-", dir="/tmp")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_dry(args, env=None, cwd=None):
    """Invoke canasta-docker in dry-run mode and return (argv_lines, stderr)."""
    base_env = os.environ.copy()
    base_env["CANASTA_DOCKER_DRY_RUN"] = "1"
    # Isolate per test so the script doesn't touch the user's real registry.
    if "CANASTA_CONFIG_DIR" not in base_env or not base_env.get(
        "CANASTA_CONFIG_DIR", ""
    ).startswith("/tmp/cd-"):
        base_env["CANASTA_CONFIG_DIR"] = tempfile.mkdtemp(
            prefix="cd-", dir="/tmp",
        )
    if env:
        base_env.update(env)
    result = subprocess.run(
        [WRAPPER] + list(args),
        env=base_env,
        cwd=cwd or "/tmp",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "wrapper failed: rc=%d\nstdout:\n%s\nstderr:\n%s"
        % (result.returncode, result.stdout, result.stderr)
    )
    return result.stdout.splitlines(), result.stderr


def assert_env_var(argv, key, value):
    """Assert that `-e KEY=VALUE` appears in the docker run argv."""
    target = "%s=%s" % (key, value)
    found = False
    for i, a in enumerate(argv):
        if a == "-e" and i + 1 < len(argv) and argv[i + 1] == target:
            found = True
            break
    assert found, "expected -e %s in argv:\n%s" % (target, "\n".join(argv))


def assert_volume_mount(argv, src, dst, ro=False):
    """Assert that `-v SRC:DST[:ro]` appears in the docker run argv."""
    target = "%s:%s%s" % (src, dst, ":ro" if ro else "")
    found = False
    for i, a in enumerate(argv):
        if a == "-v" and i + 1 < len(argv) and argv[i + 1] == target:
            found = True
            break
    assert found, "expected -v %s in argv:\n%s" % (target, "\n".join(argv))


class TestDockerHostOverride:
    """Verify the in-container docker CLI is forced to use the mounted socket."""

    def test_docker_host_env_is_set(self):
        argv, _ = run_dry(["doctor"])
        assert_env_var(argv, "DOCKER_HOST", "unix:///var/run/docker.sock")

    def test_docker_host_set_for_create(self):
        argv, _ = run_dry(["create", "-i", "x", "-w", "main"])
        assert_env_var(argv, "DOCKER_HOST", "unix:///var/run/docker.sock")


class TestBuildFromMount:
    """--build-from path should be auto-mounted into the container."""

    def test_build_from_outside_cwd_is_mounted_ro(self, short_tmp):
        # Use two non-overlapping short_tmp dirs so the wrapper's
        # "build path is already inside cwd" check doesn't fire.
        # (On Linux CI, pytest's tmp_path lives under /tmp, so using
        # the default cwd=/tmp would suppress the redundant mount.)
        cwd_dir = tempfile.mkdtemp(prefix="cd-cwd-", dir="/tmp")
        try:
            build_dir = os.path.join(short_tmp, "canasta-build")
            os.makedirs(build_dir)
            argv, _ = run_dry(
                ["create", "-i", "x", "-w", "main",
                 "--build-from", build_dir],
                cwd=cwd_dir,
            )
            assert_volume_mount(argv, build_dir, build_dir, ro=True)
        finally:
            shutil.rmtree(cwd_dir, ignore_errors=True)

    def test_build_from_relative_path_resolved_to_absolute(self, tmp_path):
        build_dir = tmp_path / "canasta-build"
        build_dir.mkdir()
        # Run with cwd=tmp_path so the relative path resolves there
        argv, _ = run_dry(
            ["create", "-i", "x", "-w", "main", "--build-from", "canasta-build"],
            cwd=str(tmp_path),
        )
        # The arg in the rewritten argv list should be absolute
        bf_idx = argv.index("--build-from")
        assert argv[bf_idx + 1] == str(build_dir)

    def test_build_from_inside_cwd_not_double_mounted(self, tmp_path):
        # When --build-from is inside $PWD, the existing $PWD mount covers it
        # and no extra -v entry should be added for the build path.
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        argv, _ = run_dry(
            ["create", "-i", "x", "-w", "main", "--build-from", "build"],
            cwd=str(tmp_path),
        )
        # Count how many -v entries reference the build dir as source
        target = "%s:%s:ro" % (str(build_dir), str(build_dir))
        v_mounts = [
            argv[i + 1] for i, a in enumerate(argv)
            if a == "-v" and i + 1 < len(argv)
        ]
        assert target not in v_mounts, (
            "build dir under cwd should not be double-mounted, found: %s"
            % v_mounts
        )


class TestSSHAgentForward:
    """SSH_AUTH_SOCK forwarding should be skipped when the socket lives in
    a path Docker Desktop's virtiofs cannot share."""

    def test_skipped_when_socket_in_launchd_tmp(self):
        # Create a real socket at a launchd-style path. /private/tmp is
        # world-writable on macOS (and a regular dir on Linux), so this
        # works on both. Path length stays well under the 104-byte
        # AF_UNIX limit.
        if not os.path.isdir("/private/tmp"):
            pytest.skip("/private/tmp not present (non-macOS layout)")
        launchd_dir = "/private/tmp/com.apple.launchd.CANASTA_TEST"
        sock_path = launchd_dir + "/Listeners"
        if os.path.lexists(sock_path):
            os.remove(sock_path)
        if os.path.isdir(launchd_dir):
            shutil.rmtree(launchd_dir)
        os.makedirs(launchd_dir)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(sock_path)
            argv, stderr = run_dry(["doctor"], env={"SSH_AUTH_SOCK": sock_path})
            forwarded = [
                argv[i + 1] for i, a in enumerate(argv)
                if a == "-v" and i + 1 < len(argv)
                and argv[i + 1].endswith(":/tmp/ssh-agent.sock")
            ]
            assert not forwarded, (
                "expected SSH agent mount to be skipped, found %s"
                % forwarded
            )
            assert "skipping SSH agent forward" in stderr
        finally:
            srv.close()
            shutil.rmtree(launchd_dir, ignore_errors=True)

    def test_forwarded_when_socket_under_short_tmp(self, short_tmp):
        # A socket at a non-launchd path should be forwarded normally.
        sock_path = os.path.join(short_tmp, "agent.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(sock_path)
            argv, stderr = run_dry(
                ["doctor"], env={"SSH_AUTH_SOCK": sock_path},
            )
            assert_volume_mount(argv, sock_path, "/tmp/ssh-agent.sock")
            assert_env_var(argv, "SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")
            assert "skipping SSH agent forward" not in stderr
        finally:
            srv.close()

    def test_no_ssh_mount_when_unset(self):
        argv, stderr = run_dry(["doctor"], env={"SSH_AUTH_SOCK": ""})
        forwarded = [
            argv[i + 1] for i, a in enumerate(argv)
            if a == "-v" and i + 1 < len(argv)
            and argv[i + 1].endswith(":/tmp/ssh-agent.sock")
        ]
        assert not forwarded
        assert "skipping SSH agent forward" not in stderr


class TestArgumentRewriting:
    """Verify the wrapper rewrites relative -p/-d/--build-from paths to
    absolute form so the container receives mountable paths."""

    def test_path_arg_resolved_to_absolute(self, tmp_path):
        target = tmp_path / "instance"
        target.mkdir()
        argv, _ = run_dry(
            ["create", "-i", "x", "-w", "main", "-p", "instance"],
            cwd=str(tmp_path),
        )
        p_idx = argv.index("-p")
        assert argv[p_idx + 1] == str(target)
