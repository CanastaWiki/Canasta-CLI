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
IMAGE_NAME = "ghcr.io/canastawiki/canasta-ansible:latest"


def user_args(argv):
    """Slice argv to just the args after the image name (i.e. the args
    canasta.py sees), so tests can look for user-supplied flags without
    matching the docker run flags that come before the image."""
    if IMAGE_NAME in argv:
        return argv[argv.index(IMAGE_NAME) + 1:]
    return argv


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


class TestGroupFileMount:
    """Verify the container gets /etc/group with host group entries."""

    def test_group_file_is_mounted(self):
        argv, _ = run_dry(["doctor"])
        group_mounts = [
            argv[i + 1] for i, a in enumerate(argv)
            if a == "-v" and i + 1 < len(argv)
            and argv[i + 1].endswith(":/etc/group:ro")
        ]
        assert group_mounts, (
            "expected /etc/group mount in argv:\n%s" % "\n".join(argv)
        )


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


class TestEnvFileMount:
    """--envfile/-e path should be auto-mounted into the container.

    See #47 — without this, files outside $PWD/$HOME are invisible
    to ansible inside the container, and even relative paths inside
    $PWD fail because ansible-playbook's cwd isn't $PWD.
    """

    def _make_envfile(self, parent):
        """Drop a minimal env file in `parent` and return its path."""
        path = os.path.join(parent, "test.env")
        with open(path, "w") as f:
            f.write("HTTP_PORT=8080\nHTTPS_PORT=8443\nCADDY_AUTO_HTTPS=off\n")
        return path

    def test_envfile_outside_cwd_is_mounted_ro(self, short_tmp):
        cwd_dir = tempfile.mkdtemp(prefix="cd-cwd-", dir="/tmp")
        try:
            envfile = self._make_envfile(short_tmp)
            envfile_dir = os.path.dirname(envfile)
            argv, _ = run_dry(
                ["create", "-i", "x", "-w", "main", "-e", envfile],
                cwd=cwd_dir,
            )
            assert_volume_mount(argv, envfile_dir, envfile_dir, ro=True)
            # Look at args passed to canasta.py (after the image name);
            # the first -e in the full argv is the docker run -e for
            # DOCKER_HOST, not the user's flag.
            uargv = user_args(argv)
            e_idx = uargv.index("-e")
            assert uargv[e_idx + 1] == envfile
        finally:
            shutil.rmtree(cwd_dir, ignore_errors=True)

    def test_envfile_long_flag_form_also_handled(self, short_tmp):
        cwd_dir = tempfile.mkdtemp(prefix="cd-cwd-", dir="/tmp")
        try:
            envfile = self._make_envfile(short_tmp)
            envfile_dir = os.path.dirname(envfile)
            argv, _ = run_dry(
                ["create", "-i", "x", "-w", "main",
                 "--envfile", envfile],
                cwd=cwd_dir,
            )
            assert_volume_mount(argv, envfile_dir, envfile_dir, ro=True)
        finally:
            shutil.rmtree(cwd_dir, ignore_errors=True)

    def test_envfile_relative_path_resolved_to_absolute(self, tmp_path):
        envfile = tmp_path / "test.env"
        envfile.write_text("HTTP_PORT=8080\n")
        argv, _ = run_dry(
            ["create", "-i", "x", "-w", "main", "-e", "test.env"],
            cwd=str(tmp_path),
        )
        # Whether or not the parent dir is mounted (it's already covered
        # by cwd), the rewritten arg should be absolute so ansible's
        # slurp module can find it regardless of its own cwd.
        uargv = user_args(argv)
        e_idx = uargv.index("-e")
        assert uargv[e_idx + 1] == str(envfile)

    def test_envfile_inside_cwd_not_double_mounted(self, tmp_path):
        envfile = tmp_path / "test.env"
        envfile.write_text("HTTP_PORT=8080\n")
        argv, _ = run_dry(
            ["create", "-i", "x", "-w", "main", "-e", "test.env"],
            cwd=str(tmp_path),
        )
        # The envfile's parent dir is $PWD (already mounted), so no
        # extra -v entry should be added for it.
        target = "%s:%s:ro" % (str(tmp_path), str(tmp_path))
        v_mounts = [
            argv[i + 1] for i, a in enumerate(argv)
            if a == "-v" and i + 1 < len(argv)
        ]
        assert target not in v_mounts, (
            "envfile parent dir under cwd should not be double-mounted, "
            "found: %s" % v_mounts
        )


class TestSSHAgentForward:
    """SSH_AUTH_SOCK forwarding should be skipped when the socket lives in
    a path Docker Desktop's virtiofs cannot share."""

    def _make_launchd_socket(self):
        """Create a real socket at a launchd-style path. /private/tmp is
        world-writable on macOS (a regular dir on Linux too). Path length
        stays well under the 104-byte AF_UNIX limit. Returns the socket
        path; caller is responsible for cleanup via _cleanup_launchd."""
        launchd_dir = "/private/tmp/com.apple.launchd.CANASTA_TEST"
        sock_path = launchd_dir + "/Listeners"
        if os.path.lexists(sock_path):
            os.remove(sock_path)
        if os.path.isdir(launchd_dir):
            shutil.rmtree(launchd_dir)
        os.makedirs(launchd_dir)
        return sock_path, launchd_dir

    def test_skipped_silently_when_socket_in_launchd_tmp(self):
        # The skip happens silently by default — passphraseless keys in
        # ~/.ssh continue to work via the $HOME mount, so the warning
        # would just be noise on every command.
        if not os.path.isdir("/private/tmp"):
            pytest.skip("/private/tmp not present (non-macOS layout)")
        sock_path, launchd_dir = self._make_launchd_socket()
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
            assert "skipping" not in stderr, (
                "skip message should be silent without "
                "CANASTA_DOCKER_VERBOSE=1, got stderr:\n%s" % stderr
            )
        finally:
            srv.close()
            shutil.rmtree(launchd_dir, ignore_errors=True)

    def test_skip_message_shown_with_verbose_env(self):
        if not os.path.isdir("/private/tmp"):
            pytest.skip("/private/tmp not present (non-macOS layout)")
        sock_path, launchd_dir = self._make_launchd_socket()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(sock_path)
            _, stderr = run_dry(
                ["doctor"],
                env={
                    "SSH_AUTH_SOCK": sock_path,
                    "CANASTA_DOCKER_VERBOSE": "1",
                },
            )
            assert "skipping agent forward" in stderr, (
                "expected verbose skip message in stderr:\n%s" % stderr
            )
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
            assert "skipping" not in stderr
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
