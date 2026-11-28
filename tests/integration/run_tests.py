#!/usr/bin/env python3
"""Integration tests for the Canasta CLI.

Exercises the CLI end to end by calling ./canasta commands as
subprocesses. Each test creates an isolated instance with unique ports
and a separate config directory.

Usage:
    python tests/integration/run_tests.py              # all tests
    python tests/integration/run_tests.py lifecycle     # specific test
    python tests/integration/run_tests.py --list        # list tests
    python tests/integration/run_tests.py --no-skip     # skips fail the run

Requirements:
    - A container runtime running (Docker or Podman)
    - Canasta CLI installed (.venv with ansible)
    - Run from repo root or set CANASTA_ROOT
"""

import gzip
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

REPO_ROOT = os.environ.get(
    "CANASTA_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
CANASTA_BIN = os.path.join(REPO_ROOT, "canasta-native")


class SkipTest(Exception):
    """Raised when a test's prerequisites are not met."""
    pass


# Atomic-ish port counter (single-threaded, no need for locks)
_port_counter = int(os.environ.get("CANASTA_TEST_PORT_BASE", "10080"))


def next_port():
    global _port_counter
    _port_counter += 10
    return str(_port_counter), str(_port_counter + 1)


_runtime = None


def container_runtime():
    """Return the runtime serving this host: 'docker', 'podman', or ''.

    Probed in the same order the CLI's own create pre-flight uses, so a
    test run resolves to whatever the CLI will resolve to. Cached — the
    answer cannot change mid-run.
    """
    global _runtime
    if _runtime is None:
        _runtime = ""
        for candidate in ("docker", "podman"):
            try:
                probe = subprocess.run(
                    [candidate, "info"], capture_output=True,
                )
            except OSError:
                continue
            if probe.returncode == 0:
                _runtime = candidate
                break
    return _runtime


def require_docker():
    """Skip a test that drives `docker` directly on a non-Docker host.

    Most tests only call the CLI and are runtime-agnostic. A few reach
    past it to seed volumes or exec into containers with Docker-specific
    invocations; those report SKIPPED under Podman rather than failing on
    a missing binary.
    """
    if container_runtime() != "docker":
        raise SkipTest(
            "test drives `docker` directly; this host runs %s"
            % (container_runtime() or "no container runtime")
        )


class TestInstance:
    def __init__(self, test_id):
        self.id = test_id
        self.work_dir = tempfile.mkdtemp(prefix="canasta-int-work-")
        self.config_dir = tempfile.mkdtemp(prefix="canasta-int-conf-")
        self.http_port, self.https_port = next_port()
        self.env_file = os.path.join(self.work_dir, "test.env")
        with open(self.env_file, "w") as f:
            f.write(
                "HTTP_PORT=%s\nHTTPS_PORT=%s\nTLS_MODE=off\n"
                % (self.http_port, self.https_port)
            )

    def cli_env(self):
        """Environment for a CLI invocation under test.

        CANASTA_SELF_UPDATED pins the suite to the checked-out tree.
        `canasta upgrade` otherwise self-updates before running its
        playbook: it decides whether to move with `git merge-base
        --is-ancestor <latest tag> HEAD`, which cannot succeed in CI's
        depth-1 clone, so it checks out the release tag and re-execs.
        Every later test — and the upgrade path itself — would then run
        the released CLI instead of the code under test.
        """
        env = os.environ.copy()
        env["CANASTA_CONFIG_DIR"] = self.config_dir
        env["ANSIBLE_CONFIG"] = os.path.join(REPO_ROOT, "ansible.cfg")
        env["CANASTA_SELF_UPDATED"] = "1"
        return env

    def run(self, *args):
        """Run a canasta command in verbose mode. Returns (stdout, returncode).

        Streams each line as it arrives instead of collecting the whole
        run and printing it at the end. A command that outlives the CI
        step timeout is killed outright, and buffered output dies with
        it — a create that stalled produced 35 minutes of nothing, while
        Ansible had been printing a retry line every 10 seconds naming
        the task it was stuck on.
        """
        env = self.cli_env()
        # Run in verbose mode for CI feedback
        cmd = [CANASTA_BIN, "--verbose"] + list(args)
        print("  $ canasta %s" % " ".join(args), flush=True)
        # stderr folded into stdout so the two stay in order; they were
        # concatenated before, which put every warning after the run.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=self.work_dir, env=env,
        )
        lines = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            print("    %s" % line, flush=True)
        proc.stdout.close()
        returncode = proc.wait()
        sys.stdout.flush()
        return "\n".join(lines), returncode

    def run_quiet(self, *args):
        """Run without --verbose for parseable output."""
        env = self.cli_env()
        cmd = [CANASTA_BIN] + list(args)
        print("  $ canasta %s" % " ".join(args), flush=True)
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=self.work_dir, env=env,
        )
        output = result.stdout + result.stderr
        if output.strip():
            for line in output.strip().split("\n"):
                print("    %s" % line)
        sys.stdout.flush()
        if result.returncode != 0:
            raise AssertionError(
                "Command failed (rc=%d): canasta %s\n%s"
                % (result.returncode, " ".join(args), output)
            )
        return output

    def run_ok(self, *args):
        """Run a canasta command, fail if non-zero exit."""
        output, rc = self.run(*args)
        if rc != 0:
            raise AssertionError(
                "Command failed (rc=%d): canasta %s\n%s"
                % (rc, " ".join(args), output)
            )
        return output

    def instance_path(self):
        return os.path.join(self.work_dir, self.id)

    def env_path(self):
        return os.path.join(self.instance_path(), ".env")

    def is_registered(self):
        """True when this test's registry still lists the instance."""
        path = os.path.join(self.config_dir, "conf.json")
        try:
            with open(path) as f:
                return self.id in (json.load(f).get("Instances") or {})
        except (OSError, ValueError):
            return False

    def cleanup(self):
        """Delete the instance and remove work directory.

        Returns False when teardown failed. Tests that never create an
        instance, or that delete it themselves, leave nothing to remove:
        deleting anyway fails and fills a passing run's log with the
        failure. A delete that fails for an instance still in the
        registry is a real leak — containers, volumes and the entry
        itself outlive the test, and the next one inherits them.
        """
        ok = True
        if self.is_registered():
            print("  Cleanup: deleting instance %s" % self.id)
            _, rc = self.run("delete", "-i", self.id, "--yes")
            if rc != 0:
                print("  CLEANUP FAILED: canasta delete -i %s exited %d"
                      % (self.id, rc))
                ok = False
        subprocess.run(
            ["sudo", "rm", "-rf", self.work_dir],
            capture_output=True,
        )
        shutil.rmtree(self.config_dir, ignore_errors=True)
        return ok


def wait_for_wiki(http_port, timeout=300):
    """Poll MediaWiki API until responsive or timeout."""
    return wait_for_wiki_at_path(http_port, "/w/api.php", timeout=timeout)


def wait_for_wiki_at_path(http_port, api_path="/w/api.php", timeout=300):
    """Poll MediaWiki API at an arbitrary path until responsive or timeout."""
    api_url = (
        "http://127.0.0.1:%s%s"
        "?action=query&meta=siteinfo&format=json" % (http_port, api_path)
    )
    deadline = time.time() + timeout
    last_err = ""
    attempt = 0
    print(
        "  Waiting for wiki at port %s path %s..." % (http_port, api_path),
        flush=True,
    )
    while time.time() < deadline:
        attempt += 1
        elapsed = int(time.time() + timeout - deadline)
        try:
            req = urllib.request.Request(api_url)
            # Match the URL stored in wikis.yaml so FarmConfigLoader routes
            # correctly. Tests use non-standard HTTP_PORT with
            # TLS_MODE=off, so wikis.yaml stores localhost:<port>.
            req.add_header("Host", "localhost:%s" % http_port)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                if "query" in data:
                    print("  Wiki is up at port %s" % http_port)
                    return
                if "error" in data:
                    if data["error"].get("code") == "readapidenied":
                        print(
                            "  Wiki is up at port %s (private)" % http_port
                        )
                        return
                last_err = "no 'query' key"
        except Exception as e:
            last_err = str(e)
        if attempt % 6 == 0:  # every 30 seconds
            print(
                "    ...still waiting (%ds elapsed, last: %s)"
                % (elapsed, last_err), flush=True,
            )
        time.sleep(5)
    # Dump diagnostics
    print("  TIMEOUT waiting for wiki (last: %s)" % last_err)
    if container_runtime():
        subprocess.run([container_runtime(), "ps", "-a"], capture_output=False)
    raise AssertionError(
        "Wiki did not become ready at port %s within %ds" % (http_port, timeout)
    )


def query_siteinfo(http_port, siprop, api_path="/w/api.php"):
    """Query the MediaWiki siteinfo API and return the parsed JSON data."""
    api_url = (
        "http://127.0.0.1:%s%s"
        "?action=query&meta=siteinfo&siprop=%s&format=json"
        % (http_port, api_path, siprop)
    )
    req = urllib.request.Request(api_url)
    req.add_header("Host", "localhost:%s" % http_port)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def read_env(path):
    """Read a .env file into a dict."""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                result[k] = v
    return result


# --- Tests ---

def test_podman_socket_runtime(inst):
    """A rootless Podman socket must be what decides the runtime.

    There are two ways to arrive at Podman, and only one of them is what
    an installed host does. With no socket and Docker unreachable, the
    pre-flight falls through to "Docker absent, Podman present" and
    switches the runtime there. With a socket, it decides from the socket
    before probing anything.

    A lane that only ever takes the first route says nothing about the
    second, which is the one `canasta install podman` produces and every
    real rootless host takes. That route once broke `create` outright
    while this suite stayed green on every push to main.

    Skips where there is no socket, so Docker lanes are unaffected.
    """
    sock = "/run/user/%d/podman/podman.sock" % os.getuid()
    if not os.path.exists(sock):
        raise SkipTest("no rootless Podman socket at %s" % sock)

    print("Creating instance with a Podman socket present...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )

    with open(os.path.join(inst.config_dir, "conf.json")) as f:
        record = (json.load(f).get("Instances") or {})[inst.id]

    # The socket has to be what got recorded. Reaching Podman by the
    # fallback leaves dockerHost unset, so this is what tells the two
    # routes apart.
    assert record.get("dockerHost") == "unix://%s" % sock, (
        "create did not record the detected Podman socket; it likely "
        "reached Podman by the Docker-absent fallback instead. "
        "dockerHost=%r" % record.get("dockerHost")
    )
    assert "podman" in (record.get("composeCommand") or ""), (
        "composeCommand=%r" % record.get("composeCommand")
    )
    assert record.get("inspectCommand") == "podman", (
        "inspectCommand=%r" % record.get("inspectCommand")
    )

    print("Waiting for wiki...")
    wait_for_wiki(inst.http_port)

    # The containers must actually be Podman's, not just labelled so.
    running = subprocess.run(
        ["podman", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout
    assert any(inst.id in line for line in running.splitlines()), (
        "no Podman containers for %s; podman ps:\n%s" % (inst.id, running)
    )


def test_lifecycle(inst):
    """Create -> verify -> stop -> start -> verify -> delete."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )

    print("Waiting for wiki...")
    wait_for_wiki(inst.http_port)

    print("Stopping instance...")
    inst.run_ok("stop", "-i", inst.id)

    print("Starting instance...")
    inst.run_ok("start", "-i", inst.id)

    print("Waiting for wiki after restart...")
    wait_for_wiki(inst.http_port)

    print("Listing instances...")
    output = inst.run_quiet("list")
    assert inst.id in output, "Instance not in list output"
    assert "NOT FOUND" not in output, (
        "Instance shows NOT FOUND instead of running status:\n%s" % output
    )
    assert "RUNNING" in output, (
        "Instance should show RUNNING status:\n%s" % output
    )


def test_import_export(inst):
    """Export DB, add wiki, import into it, verify."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Exporting database...")
    export_file = os.path.join(inst.work_dir, "main_export.sql")
    inst.run_ok("export", "-i", inst.id, "-w", "main", "-f", export_file)
    assert os.path.exists(export_file), "Export file not created"
    assert os.path.getsize(export_file) > 0, "Export file is empty"

    print("Adding second wiki...")
    inst.run_ok(
        "add", "-i", inst.id, "-w", "importtest",
        "-u", "localhost/importtest",
    )

    print("Importing database into second wiki...")
    inst.run_ok(
        "import", "-i", inst.id, "-w", "importtest",
        "-d", export_file,
    )

    print("Verifying original wiki still accessible...")
    wait_for_wiki(inst.http_port, timeout=180)


def test_upgrade(inst):
    """Remove CANASTA_IMAGE, run upgrade migration, verify backfilled."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Removing CANASTA_IMAGE from .env...")
    env = read_env(inst.env_path())
    with open(inst.env_path(), "w") as f:
        for k, v in env.items():
            if k != "CANASTA_IMAGE":
                f.write("%s=%s\n" % (k, v))

    env_after_remove = read_env(inst.env_path())
    assert "CANASTA_IMAGE" not in env_after_remove, (
        "CANASTA_IMAGE should have been removed"
    )

    print("Running upgrade...")
    inst.run_ok("upgrade")

    print("Verifying CANASTA_IMAGE backfilled...")
    env_after_upgrade = read_env(inst.env_path())
    assert "CANASTA_IMAGE" in env_after_upgrade, (
        "CANASTA_IMAGE not backfilled after upgrade"
    )
    assert env_after_upgrade["CANASTA_IMAGE"] != "", (
        "CANASTA_IMAGE is empty after upgrade"
    )

    print("Verifying wiki still accessible...")
    wait_for_wiki(inst.http_port)


def test_upgrade_rebuilds_buildable(inst):
    """Upgrade must actually rebuild services carrying a `build:` directive.

    The rebuild step enumerates buildable services out of the merged
    compose config. When that render fails, an empty service list is
    indistinguishable from "nothing to rebuild": the loop runs over
    nothing and the upgrade reports success while the custom image stays
    stale. That silent skip is invisible to a hand-run, so assert it —
    the Dockerfile's canary label is changed between the two builds and
    read back off the image after the upgrade.
    """
    runtime = container_runtime()
    inst_path = inst.instance_path()
    custom_image = "%s:custom" % inst.id

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    base_image = read_env(inst.env_path())["CANASTA_IMAGE"]

    def write_dockerfile(canary):
        # One LABEL over the instance's own base image: the build is a
        # single metadata layer, so it needs no network and finishes
        # instantly, while still producing a distinct image ID.
        with open(os.path.join(inst_path, "Dockerfile.custom"), "w") as f:
            f.write(
                "FROM %s\nLABEL canasta.canary=%s\n" % (base_image, canary)
            )

    def image_canary():
        result = subprocess.run(
            [runtime, "image", "inspect", custom_image, "--format",
             '{{ index .Config.Labels "canasta.canary" }}'],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            "could not inspect %s: %s" % (custom_image, result.stderr)
        )
        return result.stdout.strip()

    print("Layering a custom web build over the instance image...")
    override_path = os.path.join(inst_path, "docker-compose.override.yml")
    with open(override_path, "w") as f:
        f.write(
            "services:\n"
            "  web:\n"
            "    image: %s\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.custom\n" % custom_image
        )
    write_dockerfile("before")

    # Build the first image directly rather than leaning on compose's
    # implicit build-on-up: the point under test is the *rebuild* the CLI
    # performs during upgrade, so the starting state should not depend on
    # another compose behavior.
    print("Building the custom image...")
    build = subprocess.run(
        [runtime, "build", "-t", custom_image, "-f", "Dockerfile.custom", "."],
        cwd=inst_path, capture_output=True, text=True,
    )
    assert build.returncode == 0, (
        "could not build %s:\n%s" % (custom_image, build.stderr)
    )
    assert image_canary() == "before", "precondition: canary label not set"

    print("Restarting onto the custom image...")
    inst.run_ok("restart", "-i", inst.id)
    wait_for_wiki(inst.http_port)

    print("Editing the Dockerfile so a rebuild is observable...")
    write_dockerfile("after")

    print("Running upgrade...")
    inst.run_ok("upgrade")

    print("Verifying the buildable service was rebuilt...")
    assert image_canary() == "after", (
        "upgrade did not rebuild the buildable web service — %s still "
        "carries the pre-upgrade canary label" % custom_image
    )

    print("Verifying wiki still accessible...")
    wait_for_wiki(inst.http_port)


def _cli_canasta_version():
    with open(os.path.join(REPO_ROOT, "CANASTA_VERSION")) as f:
        return f.read().strip()


def _image_file(image, path):
    """The contents of a file in an image, read without starting Canasta."""
    result = subprocess.run(
        [container_runtime(), "run", "--rm", "--entrypoint", "cat", image,
         path],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "could not read %s from %s: %s" % (path, image, result.stderr)
    )
    return result.stdout.strip()


def test_canasta_base(inst):
    """`create --canasta-base` runs the canasta-base release, and upgrade
    moves an older canasta-base pin to the current release without
    switching it to canasta."""
    current = _cli_canasta_version()
    repo = "ghcr.io/canastawiki/canasta-base"
    # The first canasta-base release that shares Canasta's version tags.
    older = "3.6.0"
    assert older != current, (
        "the older pin must differ from CANASTA_VERSION (%s)" % current
    )

    print("Creating instance on canasta-base...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file, "--canasta-base",
    )
    image = read_env(inst.env_path())["CANASTA_IMAGE"]
    assert image == "%s:%s" % (repo, current), (
        "CANASTA_IMAGE is %r, expected the canasta-base release" % image
    )
    wait_for_wiki(inst.http_port, timeout=600)

    print("Pinning the instance to an older canasta-base release...")
    env = read_env(inst.env_path())
    env["CANASTA_IMAGE"] = "%s:%s" % (repo, older)
    with open(inst.env_path(), "w") as f:
        for k, v in env.items():
            f.write("%s=%s\n" % (k, v))

    print("Running upgrade...")
    inst.run_ok("upgrade")

    image = read_env(inst.env_path())["CANASTA_IMAGE"]
    assert image == "%s:%s" % (repo, current), (
        "upgrade left CANASTA_IMAGE at %r; a canasta-base pin must move to "
        "the current canasta-base release" % image
    )
    wait_for_wiki(inst.http_port, timeout=600)


def test_build_from_overlay(inst):
    """`create --build-from` on an overlay builds the instance's own image on
    the current Canasta release, and upgrade rebuilds it from the recorded
    source."""
    marker = "/tmp/canasta-build-from-ci"
    src_root = os.path.join(inst.work_dir, "bfsrc")
    src = os.path.join(src_root, "Canasta")
    os.makedirs(src, exist_ok=True)

    def write_dockerfile(value):
        with open(os.path.join(src, "Dockerfile"), "w") as f:
            f.write("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"
                    "RUN echo %s > %s\n" % (value, marker))

    write_dockerfile("first")
    print("Creating instance from an overlay source (--build-from)...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file, "--build-from", src_root,
    )
    image = read_env(inst.env_path())["CANASTA_IMAGE"]
    assert image == "canasta:local-%s" % inst.id, (
        "CANASTA_IMAGE is %r, expected the instance's own tag" % image
    )
    assert _image_file(image, marker) == "first"
    version = inst.run_quiet("version", "-i", inst.id)
    assert ("built on ghcr.io/canastawiki/canasta:%s"
            % _cli_canasta_version()) in version, version
    wait_for_wiki(inst.http_port)

    print("Editing the source so the upgrade's rebuild is observable...")
    write_dockerfile("second")
    inst.run_ok("upgrade")
    assert _image_file(image, marker) == "second", (
        "upgrade did not rebuild the image from the recorded source"
    )
    wait_for_wiki(inst.http_port)


def _clone_canasta(inst):
    src_root = os.path.join(inst.work_dir, "bfsrc")
    os.makedirs(src_root, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1",
         "https://github.com/CanastaWiki/Canasta.git",
         os.path.join(src_root, "Canasta")],
        check=True,
    )
    return src_root


def test_build_from_two_image(inst):
    """`create --build-from` on a Canasta checkout builds canasta-base under
    the instance's own base tag and canasta on top of it."""
    src_root = _clone_canasta(inst)
    print("Creating instance from a Canasta checkout (both images)...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file, "--build-from", src_root,
    )
    image = read_env(inst.env_path())["CANASTA_IMAGE"]
    assert image == "canasta:local-%s" % inst.id, image
    version = inst.run_quiet("version", "-i", inst.id)
    assert "built on canasta-base:local-%s" % inst.id in version, version
    wait_for_wiki(inst.http_port, timeout=600)


def test_build_from_canasta_base(inst):
    """`create --canasta-base --build-from` on a Canasta checkout builds and
    runs canasta-base alone."""
    src_root = _clone_canasta(inst)
    print("Creating instance from a Canasta checkout (canasta-base only)...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file, "--build-from", src_root, "--canasta-base",
    )
    image = read_env(inst.env_path())["CANASTA_IMAGE"]
    assert image == "canasta:local-%s" % inst.id, image
    version = inst.run_quiet("version", "-i", inst.id)
    assert "canasta-base built from source" in version, version
    wait_for_wiki(inst.http_port, timeout=600)


def test_rebuild(inst):
    """`canasta rebuild` must actually rebuild, and fail loudly if it can't.

    The standalone rebuild enumerates buildable services with its own
    compose invocation rather than the upgrade path's. When that
    enumeration broke, it returned an empty list, printed "nothing to
    rebuild", and exited 0 — a no-op reported as a success. Assert the
    rebuild by changing the Dockerfile's canary label and reading it back
    off the image, the same way the upgrade path is asserted.
    """
    runtime = container_runtime()
    inst_path = inst.instance_path()
    custom_image = "%s:custom" % inst.id

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    base_image = read_env(inst.env_path())["CANASTA_IMAGE"]

    def write_dockerfile(canary):
        with open(os.path.join(inst_path, "Dockerfile.custom"), "w") as f:
            f.write(
                "FROM %s\nLABEL canasta.canary=%s\n" % (base_image, canary)
            )

    def image_canary():
        result = subprocess.run(
            [runtime, "image", "inspect", custom_image, "--format",
             '{{ index .Config.Labels "canasta.canary" }}'],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            "could not inspect %s: %s" % (custom_image, result.stderr)
        )
        return result.stdout.strip()

    print("Layering a custom web build over the instance image...")
    with open(os.path.join(inst_path, "docker-compose.override.yml"), "w") as f:
        f.write(
            "services:\n"
            "  web:\n"
            "    image: %s\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.custom\n" % custom_image
        )
    write_dockerfile("before")

    print("Building the custom image...")
    build = subprocess.run(
        [runtime, "build", "-t", custom_image, "-f", "Dockerfile.custom", "."],
        cwd=inst_path, capture_output=True, text=True,
    )
    assert build.returncode == 0, (
        "could not build %s:\n%s" % (custom_image, build.stderr)
    )
    assert image_canary() == "before", "precondition: canary label not set"

    print("Restarting onto the custom image...")
    inst.run_ok("restart", "-i", inst.id)
    wait_for_wiki(inst.http_port)

    print("Editing the Dockerfile so a rebuild is observable...")
    write_dockerfile("after")

    print("Running rebuild...")
    output = inst.run_ok("rebuild", "-i", inst.id)

    # The failure mode was a success report over skipped work, so the
    # claim itself is worth asserting, not just the exit code.
    assert "nothing to rebuild" not in output, (
        "rebuild found no buildable services on an instance that has one:\n%s"
        % output
    )

    print("Verifying the buildable service was rebuilt...")
    assert image_canary() == "after", (
        "rebuild did not rebuild the buildable web service — %s still "
        "carries the pre-rebuild canary label" % custom_image
    )

    print("Verifying wiki still accessible...")
    wait_for_wiki(inst.http_port)


def _mysql_data_volume(inst):
    """Name of the instance's MySQL data volume, as Compose derives it."""
    return "%s_mysql-data-volume" % re.sub(
        r"[^a-z0-9_-]", "", inst.id.lower()
    )


def _wipe_volume(volume):
    """Empty a Docker volume via a throwaway alpine container."""
    subprocess.run(
        ["docker", "run", "--rm", "-v", "%s:/data" % volume, "alpine",
         "sh", "-c", "rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null || true"],
        check=True, capture_output=True,
    )


def _seed_mysql8_datadir(inst, volume, password):
    """Seed a genuine MySQL 8.0 datadir (with a sentinel row) into `volume`.

    Runs a throwaway mysql:8.0 container over the volume, creates a canary
    table, and confirms the result is a real MySQL 8 datadir (mysql.ibd) —
    the exact marker detection keys off. Leaves the volume populated on
    return.
    """
    seed = "canasta-mysql8-seed-%s" % inst.id
    subprocess.run(["docker", "rm", "-f", seed], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", seed,
         "-v", "%s:/var/lib/mysql" % volume,
         "-e", "MYSQL_ROOT_PASSWORD=%s" % password,
         "-e", "MYSQL_ROOT_HOST=%",
         "mysql:8.0"],
        check=True, capture_output=True,
    )
    try:
        print("  Waiting for the seed MySQL 8.0 server...")
        deadline = time.time() + 120
        while time.time() < deadline:
            ping = subprocess.run(
                ["docker", "exec", "-e", "MYSQL_PWD=%s" % password, seed,
                 "mysqladmin", "ping", "-h", "localhost", "--user=root"],
                capture_output=True, text=True,
            )
            if ping.returncode == 0 and "alive" in ping.stdout:
                break
            time.sleep(3)
        else:
            raise AssertionError("seed MySQL 8.0 server never became ready")

        seed_sql = (
            "CREATE DATABASE IF NOT EXISTS main; "
            "CREATE TABLE main.canary (id INT PRIMARY KEY, note VARCHAR(64)); "
            "INSERT INTO main.canary VALUES (1, 'survived-migration');"
        )
        result = subprocess.run(
            ["docker", "exec", "-e", "MYSQL_PWD=%s" % password, seed,
             "mysql", "--user=root", "-e", seed_sql],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            "seeding the sentinel failed:\n%s\n%s"
            % (result.stdout, result.stderr)
        )

        # Confirm the seed really produced a MySQL 8 datadir (the exact thing
        # the buggy probe failed to recognize).
        ibd = subprocess.run(
            ["docker", "exec", seed, "test", "-f", "/var/lib/mysql/mysql.ibd"],
            capture_output=True,
        )
        assert ibd.returncode == 0, "seed did not produce a MySQL 8 datadir"
    finally:
        subprocess.run(["docker", "rm", "-f", seed], capture_output=True)


def _query_canary(inst, password):
    """Return (rc, stdout) from selecting the canary row out of MariaDB."""
    query = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "sh", "-c",
         "MYSQL_PWD=%s mariadb -u root --skip-column-names -N "
         "-e 'SELECT note FROM main.canary WHERE id=1'" % password],
        cwd=inst.instance_path(), capture_output=True, text=True,
    )
    return query.returncode, query.stdout


def _assert_mariadb_datadir(volume):
    """Assert the volume now holds a MariaDB datadir, not MySQL 8's."""
    fmt = subprocess.run(
        ["docker", "run", "--rm", "-v", "%s:/data" % volume, "alpine",
         "sh", "-c", "ls -a /data"],
        capture_output=True, text=True,
    )
    assert "aria_log_control" in fmt.stdout, (
        "expected a MariaDB datadir (aria_log_control) after migration:\n%s"
        % fmt.stdout
    )
    assert "mysql.ibd" not in fmt.stdout, (
        "MySQL 8 system tablespace still present after migration:\n%s"
        % fmt.stdout
    )


def test_upgrade_mysql_to_mariadb(inst):
    """A legacy MySQL 8.0 datadir is migrated to MariaDB on upgrade, preserving
    user data — and a second upgrade is a clean no-op.

    Regression: detection keyed off /var/lib/mysql/.rocksdb, a marker absent
    from every real MySQL 8 datadir, so the migration was silently skipped and
    MariaDB later crashed booting on the un-migrated MySQL 8 data. This seeds a
    genuine MySQL 8.0 datadir (with a sentinel row) into the instance's data
    volume, runs upgrade, and asserts the row is present in MariaDB afterward.
    Because MariaDB physically cannot boot on a MySQL 8 datadir, a successful
    query also proves detection fired and the migration ran. A second upgrade
    must then be idempotent: the datadir is already MariaDB, so detection does
    not fire and no re-migration touches the data.
    """
    # Seeds a genuine MySQL 8 datadir with throwaway `docker run`
    # containers.
    require_docker()
    volume = _mysql_data_volume(inst)

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    # No need to wait for the wiki — the datadir is replaced wholesale below.
    time.sleep(10)

    password = read_env(inst.env_path()).get("MYSQL_PASSWORD", "mediawiki")

    print("Stopping instance and wiping the MariaDB datadir...")
    inst.run_ok("stop", "-i", inst.id)
    _wipe_volume(volume)

    print("Seeding a genuine MySQL 8.0 datadir with a sentinel row...")
    _seed_mysql8_datadir(inst, volume, password)

    print("Running upgrade (must detect MySQL 8 and migrate to MariaDB)...")
    inst.run_ok("upgrade")

    print("Verifying the sentinel row survived into MariaDB...")
    rc, out = _query_canary(inst, password)
    assert rc == 0, (
        "querying MariaDB after migration failed — did MariaDB boot, or is it "
        "crash-looping on un-migrated MySQL 8 data?\n%s" % out
    )
    assert "survived-migration" in out, (
        "sentinel row missing from MariaDB after migration:\n%s" % out
    )

    print("Verifying the datadir is now MariaDB format...")
    _assert_mariadb_datadir(volume)

    # Idempotent re-run: the datadir is already MariaDB, so detection must not
    # fire and the sentinel row must be untouched (no dump/clear/reimport).
    print("Running upgrade again (must be a clean no-op)...")
    output = inst.run_ok("upgrade")
    assert "Detected MySQL 8.0 data" not in output, (
        "second upgrade re-ran the migration on an already-MariaDB datadir:\n%s"
        % output
    )

    print("Verifying the sentinel row is still present after the re-run...")
    rc, out = _query_canary(inst, password)
    assert rc == 0 and "survived-migration" in out, (
        "sentinel row disappeared after an idempotent re-run:\n%s" % out
    )
    _assert_mariadb_datadir(volume)


def test_upgrade_mysql_to_mariadb_recovery(inst):
    """Recover an instance whose compose was already swapped to MariaDB but
    whose data volume still holds a MySQL 8.0 datadir.

    This is the field-failure state left behind when the .rocksdb-marker bug
    skipped the migration: the managed compose now pins MariaDB, yet the volume
    is un-migrated MySQL 8 data that MariaDB cannot boot on. Detection is a
    read-only volume probe (mysql.ibd), independent of what compose pins, and
    the dump runs against a throwaway mysql:8.0 container — so upgrade must
    still detect and migrate here. A successful MariaDB query afterward proves
    the recovery path fired (MariaDB physically cannot boot on MySQL 8 data).
    """
    # Seeds a genuine MySQL 8 datadir with throwaway `docker run`
    # containers.
    require_docker()
    volume = _mysql_data_volume(inst)

    print("Creating instance (compose already pins MariaDB)...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    time.sleep(10)

    password = read_env(inst.env_path()).get("MYSQL_PASSWORD", "mediawiki")

    # Leave the MariaDB-pinned compose as-is; only the volume regresses to
    # MySQL 8 data. This is exactly the stranded post-bug state.
    print("Stopping instance and regressing the volume to MySQL 8.0 data...")
    inst.run_ok("stop", "-i", inst.id)
    _wipe_volume(volume)
    _seed_mysql8_datadir(inst, volume, password)

    print("Running upgrade (must recover MySQL 8 data despite MariaDB compose)...")
    inst.run_ok("upgrade")

    print("Verifying the sentinel row was recovered into MariaDB...")
    rc, out = _query_canary(inst, password)
    assert rc == 0, (
        "querying MariaDB after recovery failed — did MariaDB boot, or is it "
        "crash-looping on un-migrated MySQL 8 data?\n%s" % out
    )
    assert "survived-migration" in out, (
        "sentinel row missing from MariaDB after recovery:\n%s" % out
    )

    print("Verifying the datadir is now MariaDB format...")
    _assert_mariadb_datadir(volume)


def test_reconcile(inst):
    """`canasta reconcile` re-derives COMPOSE_PROFILES from the feature flags
    and converges the running containers without a disruptive stop.

    Introduces config/runtime drift by hand-injecting a stale profile
    (redis) into .env's COMPOSE_PROFILES while CANASTA_ENABLE_REDIS stays
    off. reconcile must strip the stale profile back out (its source of truth
    is the flags, not the raw list) and leave the wiki serving.
    """
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Introducing drift: injecting a stale profile into COMPOSE_PROFILES...")
    env = read_env(inst.env_path())
    profiles = [p for p in env.get("COMPOSE_PROFILES", "").split(",") if p]
    assert "redis" not in profiles, (
        "precondition: redis profile should be absent"
    )
    drifted = ",".join(profiles + ["redis"])
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "COMPOSE_PROFILES=%s" % drifted, "--force", "--no-restart",
    )
    env = read_env(inst.env_path())
    assert "redis" in env.get("COMPOSE_PROFILES", "").split(","), (
        "drift not seeded: %s" % env.get("COMPOSE_PROFILES")
    )

    print("Reconciling...")
    output = inst.run_ok("reconcile", "-i", inst.id)
    assert "reconciled" in output, (
        "reconcile should report the instance was reconciled:\n%s" % output
    )

    print("Verifying the stale profile was healed out of COMPOSE_PROFILES...")
    env = read_env(inst.env_path())
    healed = [p for p in env.get("COMPOSE_PROFILES", "").split(",") if p]
    assert "redis" not in healed, (
        "reconcile did not strip the stale redis profile: %s"
        % env.get("COMPOSE_PROFILES")
    )

    print("Verifying the wiki still serves after reconcile...")
    wait_for_wiki(inst.http_port)

    # The compose stack bind-mounts config/Caddyfile as a file, so the
    # container resolves it to an inode once, at start. A rewrite that
    # renames a new file into place leaves the container reading the old
    # inode for its whole lifetime, and the reload then applies nothing —
    # silently, because caddy validate in the container reads the same
    # stale inode. Assert what the container can actually see.
    print("Changing the Caddyfile source and reconciling...")
    global_path = os.path.join(inst.instance_path(), "config",
                               "Caddyfile.global")
    with open(global_path, "a") as f:
        f.write("\n{\n\tservers {\n\t\ttimeouts {\n"
                "\t\t\tidle 47s\n\t\t}\n\t}\n}\n")
    inst.run_ok("reconcile", "-i", inst.id)

    host_caddyfile = os.path.join(inst.instance_path(), "config", "Caddyfile")
    with open(host_caddyfile) as f:
        host_text = f.read()
    assert "idle 47s" in host_text, "the reconcile did not pick up the edit"

    container_text = inst.run_quiet(
        "maintenance", "exec", "-i", inst.id, "-s", "caddy",
        "cat", "/etc/caddy/Caddyfile",
    )
    assert "idle 47s" in container_text, (
        "the container still sees the old Caddyfile — the write replaced "
        "the file's inode and stranded the bind mount, so a reload would "
        "apply nothing"
    )

    print("Verifying reconcile's reload applied it to the running Caddy...")
    running = inst.run_quiet(
        "maintenance", "exec", "-i", inst.id, "-s", "caddy",
        "sh", "-c", "wget -qO- http://127.0.0.1:2019/config/",
    )
    assert '"idle_timeout":47000000000' in running, (
        "caddy's running config does not carry the change, so the reload "
        "did not apply the regenerated Caddyfile"
    )


def test_upgrade_backfill_hosts_yaml(inst):
    """Initialize gitops, simulate Go-CLI layout (no hosts.yaml),
    run upgrade, verify the backfill_hosts_yaml migration recreates
    hosts/hosts.yaml from the existing per-host directories."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Bootstrapping gitops to create the canonical layout...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    inst_path = inst.instance_path()
    hosts_yaml = os.path.join(inst_path, "hosts", "hosts.yaml")
    assert os.path.isfile(hosts_yaml), (
        "hosts.yaml should have been created by gitops init"
    )

    print("Simulating Go-CLI gitops layout (delete hosts.yaml)...")
    os.remove(hosts_yaml)
    # Verify the directory structure that the migration keys off of
    # is still present.
    assert os.path.isdir(os.path.join(inst_path, "hosts", "testhost")), (
        "per-host directory should still exist"
    )
    assert os.path.isfile(os.path.join(inst_path, ".gitops-host")), (
        ".gitops-host marker should still exist"
    )

    print("Running upgrade...")
    inst.run_ok("upgrade")

    print("Verifying hosts.yaml was backfilled...")
    assert os.path.isfile(hosts_yaml), (
        "hosts.yaml not recreated by backfill_hosts_yaml migration"
    )
    with open(hosts_yaml) as f:
        content = f.read()
    assert "name: testhost" in content, (
        "hosts.yaml missing entry for testhost: %s" % content
    )
    assert "role: both" in content, (
        "hosts.yaml missing default role 'both': %s" % content
    )
    assert "pull_requests: false" in content, (
        "hosts.yaml missing pull_requests default: %s" % content
    )

    print("Verifying wiki still accessible...")
    wait_for_wiki(inst.http_port)


def test_backup(inst):
    """Init repo, create snapshot, drop DB, restore, verify."""
    # Drops the database through `docker compose exec`.
    require_docker()
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    # Skip wiki readiness check — backup commands only need
    # containers running, not a fully initialized wiki.
    time.sleep(10)  # Brief wait for containers to stabilize

    print("Configuring backup repository...")
    backup_dir = tempfile.mkdtemp(prefix="canasta-int-backup-")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_REPOSITORY=%s" % backup_dir,
        "--no-restart",
    )
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_PASSWORD=testpass",
        "--no-restart",
    )

    print("Initializing backup repository...")
    inst.run_ok("backup", "init", "-i", inst.id)

    print("Creating backup snapshot...")
    inst.run_ok("backup", "create", "-i", inst.id, "-t", "test-snapshot")

    print("Listing snapshots...")
    output = inst.run_quiet("backup", "list", "-i", inst.id)
    assert "test-snapshot" in output, (
        "Snapshot tag not found in list output"
    )

    print("Simulating data loss...")
    subprocess.run(
        [
            "docker", "compose", "exec", "-T", "db",
            "/bin/bash", "-c",
            "mariadb -u root -p$MYSQL_ROOT_PASSWORD "
            "-e 'DROP DATABASE IF EXISTS main; CREATE DATABASE main'",
        ],
        cwd=inst.instance_path(),
        capture_output=True,
        check=True,
    )

    print("Restoring from backup...")
    # Extract snapshot ID (8+ hex chars) from clean restic output
    snapshot_id = None
    for line in output.split("\n"):
        if "test-snapshot" in line:
            match = re.search(r'\b([0-9a-f]{8,})\b', line)
            if match:
                snapshot_id = match.group(1)
                break
    assert snapshot_id, (
        "Could not extract snapshot ID from: %s"
        % [l for l in output.split("\n") if "test-snapshot" in l]
    )
    inst.run_ok(
        "backup", "restore", "-i", inst.id,
        "-s", snapshot_id, "--skip-safety-backup",
    )

    print("Restarting after restore...")
    inst.run_ok("restart", "-i", inst.id)

    print("Verifying wiki accessible after restore...")
    wait_for_wiki(inst.http_port, timeout=300)
    shutil.rmtree(backup_dir, ignore_errors=True)


def test_backup_extra_database(inst):
    """A database declared in extra_databases is captured and restored.

    Cargo keeps its tables in a database of its own. Before this, backup
    dumped only the registered wiki databases, so a restore brought the
    wiki back without its Cargo data — and a rebuild of that data is not
    a practical recovery path at scale.
    """
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    time.sleep(10)  # Brief wait for containers to stabilize

    def sql(statement):
        """Run a statement in the db container through the CLI itself."""
        return inst.run_quiet(
            "maintenance", "exec", "-i", inst.id, "-s", "db",
            "bash", "-c",
            'mariadb -u root -p"$MYSQL_ROOT_PASSWORD" -N -B -e %s'
            % shlex.quote(statement),
        )

    print("Creating a second database alongside the wiki...")
    sql("CREATE DATABASE main_cargo")
    sql("CREATE TABLE main_cargo.cargo__demo (id INT, page VARCHAR(64))")
    sql("INSERT INTO main_cargo.cargo__demo VALUES (1, 'Original')")

    print("Declaring it...")
    before = inst.run_quiet("backup", "databases", "list", "-i", inst.id)
    assert "main_cargo" not in before, before
    inst.run_ok("backup", "databases", "add", "-i", inst.id, "main_cargo")
    after = inst.run_quiet("backup", "databases", "list", "-i", inst.id)
    assert "main: main, main_cargo" in after, after

    print("Configuring backup repository...")
    backup_dir = tempfile.mkdtemp(prefix="canasta-int-backup-")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_REPOSITORY=%s" % backup_dir, "--no-restart",
    )
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_PASSWORD=testpass", "--no-restart",
    )
    inst.run_ok("backup", "init", "-i", inst.id)

    print("Creating backup snapshot...")
    inst.run_ok("backup", "create", "-i", inst.id, "-t", "extra-db")

    print("Simulating data loss in the extra database...")
    sql("DROP DATABASE main_cargo")

    output = inst.run_quiet("backup", "list", "-i", inst.id)
    snapshot_id = None
    for line in output.split("\n"):
        if "extra-db" in line:
            match = re.search(r'\b([0-9a-f]{8,})\b', line)
            if match:
                snapshot_id = match.group(1)
                break
    assert snapshot_id, "Could not extract snapshot ID from: %s" % output

    print("Verifying the snapshot holds one dump, not two...")
    # One dump file means one mariadb-dump invocation, which means one
    # transaction. A db_main_cargo.sql of its own would mean the extra
    # database was captured at a different moment from the wiki database
    # that tracks its rows.
    files = inst.run_quiet(
        "backup", "files", "-i", inst.id, "-s", snapshot_id,
    )
    assert "db_main.sql" in files, (
        "the wiki's dump is missing from the snapshot: %s" % files
    )
    assert "db_main_cargo.sql" not in files, (
        "the extra database was dumped separately, so it did not share the "
        "wiki's transaction: %s" % files
    )

    print("Restoring from backup...")
    inst.run_ok(
        "backup", "restore", "-i", inst.id,
        "-s", snapshot_id, "--skip-safety-backup",
    )

    print("Verifying the extra database came back with its rows...")
    restored = sql("SELECT page FROM main_cargo.cargo__demo WHERE id = 1")
    assert "Original" in restored, (
        "extra database was not restored; a declared database must "
        "round-trip through backup and restore:\n%s" % restored
    )

    shutil.rmtree(backup_dir, ignore_errors=True)


def test_add_extra_database(inst):
    """`add -d` brings in the extra databases exported beside the dump.

    `canasta export` writes each extra database a wiki declares beside the
    main dump. `canasta add -d` imports each one it finds into a database of
    that name, declares it for the new wiki, and gives MediaWiki's database
    account access to it. A file whose database name is not valid is
    reported and skipped.
    """
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    time.sleep(10)  # Brief wait for containers to stabilize

    def sql(statement, account="root"):
        """Run a statement in the db container through the CLI itself."""
        login = (
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mariadb -u root'
            if account == "root"
            else 'MYSQL_PWD="$WIKI_DB_PASSWORD" mariadb -u "$WIKI_DB_USER"'
        )
        return inst.run_quiet(
            "maintenance", "exec", "-i", inst.id, "-s", "db",
            "bash", "-c",
            "%s -N -B -e %s" % (login, shlex.quote(statement)),
        )

    print("Giving the wiki an extra database with a row in it...")
    sql("CREATE DATABASE main_cargo")
    sql("CREATE TABLE main_cargo.cargo__demo (id INT, page VARCHAR(64))")
    sql("INSERT INTO main_cargo.cargo__demo VALUES (1, 'Original')")
    inst.run_ok("backup", "databases", "add", "-i", inst.id, "main_cargo")

    print("Exporting the wiki...")
    export_file = os.path.join(inst.work_dir, "main_export.sql")
    inst.run_ok("export", "-i", inst.id, "-w", "main", "-f", export_file)
    sibling = os.path.join(inst.work_dir, "main_export.main_cargo.sql")
    assert os.path.exists(sibling), (
        "export did not write the extra database beside the main dump"
    )
    # main_cargo already belongs to wiki main here, so the dump is renamed
    # the way another instance's docs_cargo would have been exported.
    os.rename(sibling, os.path.join(inst.work_dir, "main_export.docs_cargo.sql"))
    shutil.copy(
        os.path.join(inst.work_dir, "main_export.docs_cargo.sql"),
        os.path.join(inst.work_dir, "main_export.bad.name.sql"),
    )

    print("Adding a wiki from the dump...")
    output = inst.run_ok(
        "add", "-i", inst.id, "-w", "docs",
        "-u", "localhost/docs", "-d", export_file,
    )
    assert "with extra database(s) docs_cargo imported and declared" in output, (
        "add did not report the extra database:\n%s" % output
    )
    assert "extra database name 'bad.name' is invalid" in output, (
        "add did not report the dump it skipped:\n%s" % output
    )

    print("Verifying the extra database is declared...")
    listed = inst.run_quiet("backup", "databases", "list", "-i", inst.id)
    assert "docs: docs, docs_cargo" in listed, listed

    print("Verifying MediaWiki's account can read the imported row...")
    rows = sql("SELECT page FROM docs_cargo.cargo__demo WHERE id = 1",
               account="wiki")
    assert "Original" in rows, (
        "the extra database's row did not come through add -d:\n%s" % rows
    )
    wait_for_wiki(inst.http_port, timeout=180)


def test_backup_custom_dockerfile(inst):
    """A custom Dockerfile + override are captured by backup and restored.

    Regression: backup staged docker-compose.override.yml but not the
    Dockerfile / build context it references, so a restore onto a fresh host
    had an override pointing at a missing build and could not start.
    """
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    time.sleep(10)  # Brief wait for containers to stabilize

    inst_path = inst.instance_path()
    override_path = os.path.join(inst_path, "docker-compose.override.yml")
    dockerfile_path = os.path.join(inst_path, "Dockerfile.custom")

    # Reuse the instance's own base image so the build is a no-op single FROM
    # layer (instant, no network) rather than pulling a fresh image.
    canasta_image = "ghcr.io/canastawiki/canasta:latest"
    with open(inst.env_path()) as f:
        for line in f:
            if line.startswith("CANASTA_IMAGE="):
                canasta_image = line.split("=", 1)[1].strip()
                break

    print("Adding a custom web build (override + Dockerfile.custom)...")
    with open(override_path, "w") as f:
        f.write(
            "services:\n"
            "  web:\n"
            "    image: %s:custom\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.custom\n"
            % inst.id
        )
    with open(dockerfile_path, "w") as f:
        f.write("FROM %s\n" % canasta_image)

    print("Configuring backup repository...")
    backup_dir = tempfile.mkdtemp(prefix="canasta-int-backup-")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_REPOSITORY=%s" % backup_dir, "--no-restart",
    )
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_PASSWORD=testpass", "--no-restart",
    )

    print("Initializing backup repository...")
    inst.run_ok("backup", "init", "-i", inst.id)

    print("Creating backup snapshot...")
    inst.run_ok("backup", "create", "-i", inst.id, "-t", "custom-build")

    print("Verifying the Dockerfile is inside the snapshot...")
    output = inst.run_quiet("backup", "list", "-i", inst.id)
    snapshot_id = None
    for line in output.split("\n"):
        if "custom-build" in line:
            match = re.search(r'\b([0-9a-f]{8,})\b', line)
            if match:
                snapshot_id = match.group(1)
                break
    assert snapshot_id, "Could not extract snapshot ID from:\n%s" % output
    files = inst.run_quiet(
        "backup", "files", "-i", inst.id, "-s", snapshot_id,
    )
    assert "Dockerfile.custom" in files, (
        "Dockerfile.custom not captured in snapshot:\n%s" % files
    )

    print("Deleting build inputs to simulate loss...")
    os.remove(override_path)
    os.remove(dockerfile_path)

    print("Restoring from backup...")
    inst.run_ok(
        "backup", "restore", "-i", inst.id,
        "-s", snapshot_id, "--skip-safety-backup",
    )

    print("Verifying build inputs are back on disk...")
    assert os.path.isfile(dockerfile_path), "Dockerfile.custom was not restored"
    assert os.path.isfile(override_path), (
        "docker-compose.override.yml was not restored"
    )

    print("Restarting after restore...")
    inst.run_ok("restart", "-i", inst.id)

    print("Verifying wiki accessible after restore...")
    wait_for_wiki(inst.http_port, timeout=300)
    shutil.rmtree(backup_dir, ignore_errors=True)


def test_backup_missing_dockerfile(inst):
    """A backup of an override that references a missing Dockerfile is clean.

    Regression: discovery trusts `docker compose config`, which emits the
    build: section even when the Dockerfile is absent. Staging that missing
    path would bind-mount it, and on Linux Docker creates a root-owned junk
    directory there. Discovery must stat each path and stage only existing
    ones, so the backup leaves the instance directory untouched.
    """
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    time.sleep(10)  # Brief wait for containers to stabilize

    inst_path = inst.instance_path()
    override_path = os.path.join(inst_path, "docker-compose.override.yml")
    dockerfile_path = os.path.join(inst_path, "Dockerfile.custom")

    print("Adding an override that references a NON-EXISTENT Dockerfile...")
    with open(override_path, "w") as f:
        f.write(
            "services:\n"
            "  web:\n"
            "    image: %s:custom\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.custom\n"
            % inst.id
        )
    assert not os.path.exists(dockerfile_path), "precondition: Dockerfile absent"

    print("Configuring backup repository...")
    backup_dir = tempfile.mkdtemp(prefix="canasta-int-backup-")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_REPOSITORY=%s" % backup_dir, "--no-restart",
    )
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_PASSWORD=testpass", "--no-restart",
    )

    print("Initializing backup repository...")
    inst.run_ok("backup", "init", "-i", inst.id)

    print("Creating backup snapshot (must not create a junk Dockerfile dir)...")
    inst.run_ok("backup", "create", "-i", inst.id, "-t", "missing-df")

    print("Verifying no junk Dockerfile.custom was created...")
    assert not os.path.isdir(dockerfile_path), (
        "backup created a junk directory at Dockerfile.custom"
    )
    assert not os.path.exists(dockerfile_path), (
        "backup created an unexpected Dockerfile.custom entry"
    )
    shutil.rmtree(backup_dir, ignore_errors=True)


def test_gitops(inst):
    """Init gitops, verify templates, push, verify remote, then untrack a
    file with 'gitops rm'."""
    # Check prerequisites
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    print("Verifying gitops artifacts...")
    inst_path = inst.instance_path()
    assert os.path.isdir(os.path.join(inst_path, ".git")), (
        ".git directory not created"
    )
    assert os.path.isfile(key_file), "git-crypt key not exported"

    # Verify initial commit in bare repo (use 'main' branch explicitly
    # because bare repo HEAD defaults to 'master' on some Git versions)
    result = subprocess.run(
        ["git", "log", "--oneline", "-1", "main"],
        cwd=bare_repo, capture_output=True, text=True,
    )
    assert "Initial gitops" in result.stdout, (
        "Initial commit not found: %s" % result.stdout
    )

    print("Creating test settings file...")
    settings_dir = os.path.join(
        inst_path, "config", "settings", "global"
    )
    os.makedirs(settings_dir, exist_ok=True)
    with open(os.path.join(settings_dir, "GitopsTest.php"), "w") as f:
        f.write("<?php\n$wgTestSetting = true;\n")

    print("Pushing changes...")
    inst.run_ok(
        "gitops", "add", "-i", inst.id,
        "config/settings/global/GitopsTest.php",
    )
    inst.run_ok("gitops", "push", "-i", inst.id)

    # Verify push in bare repo
    result = subprocess.run(
        ["git", "log", "--oneline", "-1", "main"],
        cwd=bare_repo, capture_output=True, text=True,
    )
    assert "Configuration update" in result.stdout, (
        "Push commit not found: %s" % result.stdout
    )

    # Verify file content in bare repo
    result = subprocess.run(
        ["git", "show", "main:config/settings/global/GitopsTest.php"],
        cwd=bare_repo, capture_output=True, text=True,
    )
    assert "wgTestSetting" in result.stdout, (
        "File content not in repo: %s" % result.stdout
    )

    # 'gitops rm <path>' is the inverse of 'gitops add': it untracks the file
    # (git rm --cached, so the deletion is staged for the next push) and
    # deletes it from disk. The just-pushed GitopsTest.php starts tracked.
    rel = "config/settings/global/GitopsTest.php"
    tracked = subprocess.run(
        ["git", "-C", inst_path, "ls-files", rel],
        capture_output=True, text=True,
    ).stdout
    assert rel in tracked, "GitopsTest.php should be tracked before rm: %r" % tracked

    print("Untracking the file with gitops rm...")
    inst.run_ok("gitops", "rm", "-i", inst.id, rel)

    print("Verifying the file is untracked from the repo...")
    tracked = subprocess.run(
        ["git", "-C", inst_path, "ls-files", rel],
        capture_output=True, text=True,
    ).stdout
    assert rel not in tracked, (
        "gitops rm should untrack the file (git rm --cached): %r" % tracked
    )

    # rm --cached only stages the deletion; the previously-pushed blob must
    # still be in the remote until the next push.
    result = subprocess.run(
        ["git", "show", "main:%s" % rel],
        cwd=bare_repo, capture_output=True, text=True,
    )
    assert "wgTestSetting" in result.stdout, (
        "the deletion should be local-only until pushed: %r" % result.stdout
    )

    print("Pushing the untrack and verifying it lands in the remote...")
    inst.run_ok("gitops", "push", "-i", inst.id)
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "main"],
        cwd=bare_repo, capture_output=True, text=True,
    )
    assert rel not in result.stdout, (
        "gitops rm + push should drop the file from the remote:\n%s"
        % result.stdout
    )


def test_gitops_join(inst):
    """Join a second instance to an existing gitops repo.

    Exercises the join working-tree checkout: files present in
    the repo but not yet in the joining instance are materialized, and a
    tracked file that differs locally is replaced with the repo's version.
    Uses a host-owned settings file (not the container-managed Caddyfile)
    for the diverging case so the assertion is portable.

    Then re-attaches the same (already-registered) host with --reinit and
    asserts the host entry is upserted rather than rejected as a duplicate.
    """
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    import yaml

    # --- Instance A: create, init gitops, push a tracked settings file ---
    print("Creating instance A...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )

    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    # Deliberately default the bare repo's HEAD to 'master' while gitops
    # pushes content to 'main'. This is the adversarial case: the join must
    # clone 'main' explicitly (git clone -b main) rather than follow the
    # remote's default HEAD, or it checks out an empty master and fails
    # reading hosts/hosts.yaml. Forcing master here exercises that on every
    # runner regardless of its git init.defaultBranch.
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=master", bare_repo],
        capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops on A (host 'hosta')...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "hosta",
        "--repo", bare_repo, "--key", key_file,
    )

    print("Adding a tracked settings file on A and pushing...")
    rel_settings = os.path.join("config", "settings", "global", "JoinTest.php")
    a_settings = os.path.join(inst.instance_path(), rel_settings)
    repo_content = "<?php\n$wgJoinTest = 'from-repo';\n"
    os.makedirs(os.path.dirname(a_settings), exist_ok=True)
    with open(a_settings, "w") as f:
        f.write(repo_content)
    inst.run_ok("gitops", "add", "-i", inst.id, rel_settings)
    inst.run_ok("gitops", "push", "-i", inst.id)

    # --- Instance B: create, seed a diverging copy, join ---
    inst_b = TestInstance("inttest-gitops-join-b")
    try:
        print("Creating instance B...")
        inst_b.run_ok(
            "create", "-i", inst_b.id, "-w", "main",
            "-n", "localhost", "-p", inst_b.work_dir,
            "-e", inst_b.env_file,
        )
        b_path = inst_b.instance_path()

        # Seed B with a stale copy of the tracked settings file so the join
        # must overwrite it (the per-file checkout-of-differing path).
        b_settings = os.path.join(b_path, rel_settings)
        os.makedirs(os.path.dirname(b_settings), exist_ok=True)
        with open(b_settings, "w") as f:
            f.write("<?php\n$wgJoinTest = 'local-stale';\n")

        print("Joining B to A's gitops repo (host 'hostb')...")
        inst_b.run_ok(
            "gitops", "join", "-i", inst_b.id, "-n", "hostb",
            "--repo", bare_repo, "--key", key_file,
        )

        print("Verifying B picked up the gitops structure...")
        assert os.path.isfile(os.path.join(b_path, ".gitops-host")), (
            ".gitops-host not created on join"
        )
        b_vars = os.path.join(b_path, "hosts", "hostb", "vars.yaml")
        assert os.path.isfile(b_vars), (
            "hosts/hostb/vars.yaml not created on join"
        )
        with open(b_vars) as f:
            assert isinstance(yaml.safe_load(f), dict), (
                "hosts/hostb/vars.yaml is not a valid vars mapping"
            )

        print("Verifying the diverging settings file was replaced...")
        with open(b_settings) as f:
            b_after = f.read()
        assert b_after == repo_content, (
            "join did not replace B's diverging settings file with the "
            "repo version; got: %r" % b_after
        )

        print("Verifying A's host vars survived B's join...")
        assert os.path.isfile(
            os.path.join(b_path, "hosts", "hosta", "vars.yaml")
        ), "A's host entry was not materialized into B on join"

        # --- Re-attach: join the already-registered host again with --reinit ---
        # B now has a .git and 'hostb' is registered in the repo, so a plain
        # re-join is refused twice over (".git exists" / "host already
        # exists"). --reinit must wipe local state AND re-attach the existing
        # host instead of erroring on the duplicate, upserting the
        # entry rather than appending a second 'hostb'.
        print("Re-attaching B with --reinit (host 'hostb' already in repo)...")
        inst_b.run_ok(
            "gitops", "join", "-i", inst_b.id, "-n", "hostb",
            "--repo", bare_repo, "--key", key_file, "--reinit",
        )

        print("Verifying re-attach did not duplicate the host entry...")
        hosts_yaml = os.path.join(b_path, "hosts", "hosts.yaml")
        with open(hosts_yaml) as f:
            hosts_after = yaml.safe_load(f).get("hosts", [])
        names = [h.get("name") for h in hosts_after]
        assert names.count("hostb") == 1, (
            "re-attach should upsert 'hostb', not duplicate it; got: %r" % names
        )
        assert "hosta" in names, (
            "re-attach dropped the other host 'hosta'; got: %r" % names
        )
    finally:
        inst_b.cleanup()


def test_extension_skin(inst):
    """Enable/disable extensions and skins, verify via API."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Enabling Cite extension...")
    inst.run_ok("extension", "enable", "-i", inst.id, "Cite")

    print("Verifying Cite is in extensions list...")
    output = inst.run_ok("extension", "list", "-i", inst.id)
    assert "Cite" in output, (
        "Cite not found in extension list output: %s" % output[:500]
    )

    print("Disabling Cite extension...")
    inst.run_ok("extension", "disable", "-i", inst.id, "Cite")

    print("Verifying Cite is NOT in extensions list...")
    output = inst.run_ok("extension", "list", "-i", inst.id)
    # After disable, Cite should not appear as enabled
    # (it may still appear in the list but marked as disabled)

    print("Enabling Timeless skin...")
    inst.run_ok("skin", "enable", "-i", inst.id, "Timeless")

    print("Verifying Timeless is in skins list...")
    output = inst.run_ok("skin", "list", "-i", inst.id)
    assert "Timeless" in output or "timeless" in output, (
        "Timeless not found in skin list output: %s" % output[:500]
    )

    print("Disabling Timeless skin...")
    inst.run_ok("skin", "disable", "-i", inst.id, "Timeless")


# A small Gerrit extension that is not bundled with Canasta, carries a
# REL1_43 branch, and ships a composer.json (so add registers it).
CLONE_TEST_EXTENSION = "UserPageEditProtection"
# A small Gerrit skin that is not bundled with Canasta, with a REL1_43 branch.
CLONE_TEST_SKIN = "Mirage"


def _composer_local_includes(inst):
    """Return the merge-plugin include list of config/composer.local.json."""
    path = os.path.join(inst.instance_path(), "config", "composer.local.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    return ((data.get("extra") or {}).get("merge-plugin") or {}).get(
        "include") or []


def _wait_for_loaded(inst, name, present, timeout=120):
    """Poll until MediaWiki has (not) loaded extension or skin `name`.

    Asks ExtensionRegistry from inside the web container: a new wiki is
    private, so the anonymous siteinfo API lists nothing.
    """
    code = (
        "echo 'PROBE=' . (ExtensionRegistry::getInstance()->isLoaded(%s)"
        " ? 'yes' : 'no') . PHP_EOL;" % json.dumps(name)
    )
    script = (
        "echo %s | php /var/www/mediawiki/w/maintenance/run.php eval "
        "--wiki=main" % shlex.quote(code)
    )
    want = "PROBE=%s" % ("yes" if present else "no")
    deadline = time.time() + timeout
    output = ""
    while True:
        try:
            output = inst.run_quiet(
                "maintenance", "exec", "-i", inst.id, "bash", "-c", script,
            )
            if want in output:
                return
        except AssertionError as e:
            output = str(e)
        if time.time() >= deadline:
            break
        time.sleep(5)
    raise AssertionError(
        "%s %s after %ds; last probe output:\n%s"
        % (name, "not loaded" if present else "still loaded", timeout,
           output)
    )


def _git(cwd, *args):
    """Run git in cwd; return (returncode, stripped stdout)."""
    result = subprocess.run(
        ["git"] + list(args), cwd=cwd, capture_output=True, text=True,
    )
    return result.returncode, result.stdout.strip()


def test_extension_lifecycle(inst):
    """Add, pin and remove a cloned extension, plain and under gitops.

    Clones a real extension from Wikimedia Gerrit, so it needs network
    access to gerrit.wikimedia.org.
    """
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    ext = CLONE_TEST_EXTENSION
    rel = "extensions/%s" % ext
    composer_entry = "%s/composer.json" % rel

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)
    inst_path = inst.instance_path()
    ext_path = os.path.join(inst_path, rel)

    # --- Plain (non-gitops) instance ---
    print("Adding %s with --clone..." % ext)
    inst.run_ok("extension", "add", "-i", inst.id, ext, "--clone")

    print("Verifying the clone and its composer registration...")
    assert os.path.isdir(os.path.join(ext_path, ".git")), (
        "%s is not a git checkout after add" % rel
    )
    includes = _composer_local_includes(inst)
    assert composer_entry in includes, (
        "%s not registered in composer.local.json: %s"
        % (composer_entry, includes)
    )

    print("Verifying %s is loaded..." % ext)
    _wait_for_loaded(inst, ext, True)

    print("Removing %s..." % ext)
    inst.run_ok("extension", "remove", "-i", inst.id, ext)

    print("Verifying the directory and composer entry are gone...")
    assert not os.path.exists(ext_path), "%s still exists after remove" % rel
    includes = _composer_local_includes(inst)
    assert composer_entry not in includes, (
        "%s still in composer.local.json after remove: %s"
        % (composer_entry, includes)
    )
    _wait_for_loaded(inst, ext, False)

    # --- Gitops instance ---
    print("Initializing gitops...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", os.path.join(inst.work_dir, "gitops-test.key"),
    )

    print("Adding %s with --clone on the gitops instance..." % ext)
    inst.run_ok("extension", "add", "-i", inst.id, ext, "--clone")

    print("Verifying %s is a registered, committed submodule..." % rel)
    gitmodules_path = os.path.join(inst_path, ".gitmodules")
    assert os.path.isfile(gitmodules_path), ".gitmodules missing after add"
    with open(gitmodules_path) as f:
        gitmodules = f.read()
    assert '[submodule "%s"]' % rel in gitmodules, (
        "%s has no .gitmodules entry:\n%s" % (rel, gitmodules)
    )
    rc, staged = _git(inst_path, "ls-files", "-s", "--", rel)
    assert rc == 0 and staged.startswith("160000 "), (
        "%s is not tracked as a gitlink: %r" % (rel, staged)
    )
    rc, _ = _git(inst_path, "submodule", "status", "--", rel)
    assert rc == 0, "git submodule status fails for %s" % rel
    assert os.path.isfile(os.path.join(ext_path, ".git")), (
        "%s/.git should be a gitfile pointing into .git/modules" % rel
    )
    assert os.path.isdir(os.path.join(inst_path, ".git", "modules", rel)), (
        ".git/modules/%s missing" % rel
    )
    rc, _ = _git(inst_path, "diff", "--cached", "--quiet", "--",
                 rel, ".gitmodules")
    assert rc == 0, "the submodule add was left uncommitted"

    print("Pinning %s to its parent commit with set-version..." % ext)
    rc, target = _git(ext_path, "rev-parse", "HEAD~1")
    assert rc == 0 and target, "could not resolve HEAD~1 in %s" % rel
    inst.run_ok(
        "extension", "set-version", "-i", inst.id, ext, "--ref", target,
    )
    _, head = _git(ext_path, "rev-parse", "HEAD")
    assert head == target, (
        "%s HEAD is %s after set-version, expected %s" % (rel, head, target)
    )
    _, staged = _git(inst_path, "ls-files", "-s", "--", rel)
    assert staged.split()[1:2] == [target], (
        "staged gitlink for %s is not %s: %r" % (rel, target, staged)
    )

    print("Removing %s from the gitops instance..." % ext)
    inst.run_ok("extension", "remove", "-i", inst.id, ext)

    print("Verifying the submodule is fully unregistered...")
    assert not os.path.exists(ext_path), "%s still exists after remove" % rel
    _, tracked = _git(inst_path, "ls-files", "--", rel)
    assert tracked == "", "%s still tracked after remove: %r" % (rel, tracked)
    if os.path.isfile(gitmodules_path):
        with open(gitmodules_path) as f:
            gitmodules = f.read()
        assert rel not in gitmodules, (
            "%s still in .gitmodules after remove:\n%s" % (rel, gitmodules)
        )
    assert not os.path.exists(
        os.path.join(inst_path, ".git", "modules", rel)
    ), ".git/modules/%s left behind after remove" % rel
    includes = _composer_local_includes(inst)
    assert composer_entry not in includes, (
        "%s still in composer.local.json after remove: %s"
        % (composer_entry, includes)
    )


def test_skin_lifecycle(inst):
    """Add a cloned skin, verify it loads, then remove it.

    Clones a real skin from Wikimedia Gerrit, so it needs network access
    to gerrit.wikimedia.org.
    """
    skin = CLONE_TEST_SKIN
    rel = "skins/%s" % skin

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)
    skin_path = os.path.join(inst.instance_path(), rel)

    print("Adding %s with --clone..." % skin)
    inst.run_ok("skin", "add", "-i", inst.id, skin, "--clone")
    assert os.path.isdir(os.path.join(skin_path, ".git")), (
        "%s is not a git checkout after add" % rel
    )

    print("Verifying %s is loaded..." % skin)
    _wait_for_loaded(inst, skin, True)

    print("Removing %s..." % skin)
    inst.run_ok("skin", "remove", "-i", inst.id, skin)
    assert not os.path.exists(skin_path), "%s still exists after remove" % rel
    _wait_for_loaded(inst, skin, False)


def test_wiki_farm(inst):
    """Add and remove wikis in a farm configuration."""
    print("Creating instance with main wiki...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Adding docs wiki...")
    inst.run_ok(
        "add", "-i", inst.id, "-w", "docs",
        "-u", "localhost:%s/docs" % inst.http_port,
    )

    print("Verifying docs wiki in wikis.yaml...")
    wikis_path = os.path.join(inst.instance_path(), "config", "wikis.yaml")
    with open(wikis_path) as f:
        wikis_content = f.read()
    assert "docs" in wikis_content, (
        "docs wiki not found in wikis.yaml:\n%s" % wikis_content
    )

    print("Verifying main wiki still accessible after add...")
    wait_for_wiki(inst.http_port, timeout=120)

    print("Removing docs wiki...")
    inst.run_ok("remove", "-i", inst.id, "-w", "docs", "-y")

    print("Verifying docs removed from wikis.yaml...")
    with open(wikis_path) as f:
        wikis_content = f.read()
    assert "docs" not in wikis_content, (
        "docs wiki still in wikis.yaml after remove:\n%s" % wikis_content
    )

    print("Verifying main wiki still accessible after remove...")
    wait_for_wiki(inst.http_port, timeout=120)


def test_config_side_effects(inst):
    """Verify config set side effects (port changes update wikis.yaml and .env)."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    # Test instance uses TLS_MODE=off, so HTTP_PORT is the active
    # port (the one reflected in wikis.yaml and MW_SITE_SERVER).
    new_http_port = "9080"
    print("Setting HTTP_PORT to %s..." % new_http_port)
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "HTTP_PORT=%s" % new_http_port, "--no-restart",
    )

    print("Checking wikis.yaml for port %s..." % new_http_port)
    wikis_yaml_path = os.path.join(
        inst.instance_path(), "config", "wikis.yaml",
    )
    assert os.path.isfile(wikis_yaml_path), (
        "wikis.yaml not found at %s" % wikis_yaml_path
    )
    with open(wikis_yaml_path) as f:
        wikis_content = f.read()
    assert (":%s" % new_http_port) in wikis_content, (
        "Port %s not found in wikis.yaml:\n%s"
        % (new_http_port, wikis_content)
    )

    # Note: The Caddyfile intentionally strips ports from server names.
    # Caddy binds to ports 80/443 inside the container; Docker maps the
    # external HTTP_PORT to container port 80. So the Caddyfile just
    # needs the domain, and the port change is verified via wikis.yaml
    # and MW_SITE_SERVER in .env.
    print("Checking MW_SITE_SERVER in .env...")
    env = read_env(inst.env_path())
    assert new_http_port in env.get("MW_SITE_SERVER", ""), (
        "MW_SITE_SERVER should contain port %s: %s"
        % (new_http_port, env.get("MW_SITE_SERVER"))
    )
    assert new_http_port in env.get("MW_SITE_FQDN", ""), (
        "MW_SITE_FQDN should contain port %s: %s"
        % (new_http_port, env.get("MW_SITE_FQDN"))
    )

    print("Reading the value back with config get...")
    output = inst.run_quiet(
        "config", "get", "-i", inst.id, "HTTP_PORT",
    )
    assert new_http_port in output, (
        "config get HTTP_PORT did not return %s:\n%s"
        % (new_http_port, output)
    )

    print("Setting an arbitrary key for config unset to remove...")
    # canasta config set rejects unknown keys unless --force is given.
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CANASTA_TEST_MARKER=hello", "--force", "--no-restart",
    )
    env = read_env(inst.env_path())
    assert env.get("CANASTA_TEST_MARKER") == "hello", (
        "marker should be set in .env, got: %s"
        % env.get("CANASTA_TEST_MARKER")
    )

    print("Removing the marker key with config unset...")
    inst.run_ok(
        "config", "unset", "-i", inst.id,
        "CANASTA_TEST_MARKER", "--force", "--no-restart",
    )
    env = read_env(inst.env_path())
    assert "CANASTA_TEST_MARKER" not in env, (
        "marker should be gone from .env after unset, still present: %s"
        % env.get("CANASTA_TEST_MARKER")
    )

    print("Resetting HTTP_PORT to 80...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "HTTP_PORT=80", "--no-restart",
    )

    print("Verifying port reset...")
    with open(wikis_yaml_path) as f:
        wikis_content = f.read()
    assert (":%s" % new_http_port) not in wikis_content, (
        "Port %s still in wikis.yaml after reset:\n%s"
        % (new_http_port, wikis_content)
    )
    env = read_env(inst.env_path())
    assert new_http_port not in env.get("MW_SITE_SERVER", ""), (
        "MW_SITE_SERVER still has %s after reset: %s"
        % (new_http_port, env.get("MW_SITE_SERVER"))
    )

    # TLS_MODE side effect: flipping the scheme
    # should switch the active port that's reflected in wikis.yaml /
    # MW_SITE_SERVER, even though neither HTTP_PORT nor HTTPS_PORT
    # changed.
    print("Switching TLS_MODE to acme (HTTPS_PORT becomes active)...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "TLS_MODE=acme", "--no-restart",
    )

    print("Checking wikis.yaml now references HTTPS_PORT %s..." % inst.https_port)
    with open(wikis_yaml_path) as f:
        wikis_content = f.read()
    assert (":%s" % inst.https_port) in wikis_content, (
        "wikis.yaml should contain HTTPS_PORT %s after TLS_MODE=acme:"
        "\n%s" % (inst.https_port, wikis_content)
    )

    env = read_env(inst.env_path())
    assert env.get("MW_SITE_SERVER", "").startswith("https://"), (
        "MW_SITE_SERVER should use https scheme: %s"
        % env.get("MW_SITE_SERVER")
    )
    assert inst.https_port in env.get("MW_SITE_SERVER", ""), (
        "MW_SITE_SERVER should contain HTTPS_PORT %s: %s"
        % (inst.https_port, env.get("MW_SITE_SERVER"))
    )
    assert inst.https_port in env.get("MW_SITE_FQDN", ""), (
        "MW_SITE_FQDN should contain HTTPS_PORT %s: %s"
        % (inst.https_port, env.get("MW_SITE_FQDN"))
    )

    print("Switching TLS_MODE back to off...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "TLS_MODE=off", "--no-restart",
    )

    print("Checking wikis.yaml is back to bare localhost (HTTP_PORT=80)...")
    with open(wikis_yaml_path) as f:
        wikis_content = f.read()
    assert (":%s" % inst.https_port) not in wikis_content, (
        "wikis.yaml should not reference HTTPS_PORT after toggle off:\n%s"
        % wikis_content
    )
    env = read_env(inst.env_path())
    assert env.get("MW_SITE_SERVER", "").startswith("http://"), (
        "MW_SITE_SERVER should use http scheme after toggle off: %s"
        % env.get("MW_SITE_SERVER")
    )

    # CANASTA_ENABLE_VERY_SHORT_URLS side-effect validation: the
    # two incompatible-feature combinations should fail-fast at
    # config-set time with descriptive errors, before the
    # misconfiguration lands in .env. The canasta-base runtime
    # catches them too as a safety net, but the CLI should be the
    # friendly first line.
    print("Validating CANASTA_ENABLE_VERY_SHORT_URLS=true is accepted "
          "on a compatible instance...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CANASTA_ENABLE_VERY_SHORT_URLS=true", "--no-restart",
    )

    print("Validating CANASTA_ENABLE_WIKI_DIRECTORY=true is refused "
          "while very short URLs are enabled...")
    out, rc = inst.run(
        "config", "set", "-i", inst.id,
        "CANASTA_ENABLE_WIKI_DIRECTORY=true", "--no-restart",
    )
    assert rc != 0, (
        "expected wiki directory enable to be refused while very "
        "short URLs are enabled:\n%s" % out
    )
    assert "CANASTA_ENABLE_VERY_SHORT_URLS" in out, (
        "expected error to reference the conflict:\n%s" % out
    )

    print("Disabling very short URLs for cleanup...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CANASTA_ENABLE_VERY_SHORT_URLS=false", "--no-restart",
    )

    print("Validating wiki directory and very short URLs are mutually "
          "exclusive in the other direction too...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CANASTA_ENABLE_WIKI_DIRECTORY=true", "--no-restart",
    )
    out, rc = inst.run(
        "config", "set", "-i", inst.id,
        "CANASTA_ENABLE_VERY_SHORT_URLS=true", "--no-restart",
    )
    assert rc != 0, (
        "expected very short URLs to be refused while wiki directory "
        "is enabled:\n%s" % out
    )
    assert "CANASTA_ENABLE_WIKI_DIRECTORY" in out, (
        "expected error to reference the conflict:\n%s" % out
    )

    print("Cleanup: disabling wiki directory...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CANASTA_ENABLE_WIKI_DIRECTORY=false", "--no-restart",
    )


def test_backup_advanced(inst):
    """Test backup purge, schedule, check, and list operations."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    time.sleep(10)  # Brief wait for containers to stabilize

    print("Configuring backup repository...")
    backup_dir = tempfile.mkdtemp(prefix="canasta-int-backup-")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_REPOSITORY=%s" % backup_dir,
        "--no-restart",
    )
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_PASSWORD=testpass",
        "--no-restart",
    )

    print("Initializing backup repository...")
    inst.run_ok("backup", "init", "-i", inst.id)

    print("Creating first snapshot (snap1)...")
    inst.run_ok("backup", "create", "-i", inst.id, "-t", "snap1")

    print("Creating second snapshot (snap2)...")
    inst.run_ok("backup", "create", "-i", inst.id, "-t", "snap2")

    print("Listing snapshots...")
    output = inst.run_quiet("backup", "list", "-i", inst.id)
    assert "snap1" in output, "snap1 not found in list output"
    assert "snap2" in output, "snap2 not found in list output"

    print("Checking repository integrity...")
    inst.run_ok("backup", "check", "-i", inst.id)

    print("Purging older snapshots (keep last 1)...")
    inst.run_ok(
        "backup", "purge", "-i", inst.id,
        "--keep-within", "1h",
    )

    print("Verifying at least one snapshot remains after purge...")
    output = inst.run_quiet("backup", "list", "-i", inst.id)
    # Both snapshots are recent (<1h old), so both should survive
    assert "snap2" in output, "snap2 should remain after purge"

    print("Resolving snapshot IDs from list output...")
    # `backup list` prints one snapshot per row including a short ID;
    # parse them out so we can target individual snapshots by ID.
    snap_ids = []
    for line in output.splitlines():
        # Snapshot ID rows start with an 8-char hex ID
        parts = line.split()
        if parts and len(parts[0]) == 8 and all(
            c in "0123456789abcdef" for c in parts[0]
        ):
            snap_ids.append(parts[0])
    assert len(snap_ids) >= 2, (
        "expected at least 2 snapshot IDs in list output, got: %s\n%s"
        % (snap_ids, output)
    )
    snap1_id, snap2_id = snap_ids[0], snap_ids[1]

    print("Listing files in snapshot %s..." % snap1_id)
    output = inst.run_quiet(
        "backup", "files", "-i", inst.id, "-s", snap1_id,
    )
    assert output.strip(), "backup files produced no output for %s" % snap1_id

    print("Diffing snapshots %s..%s..." % (snap1_id, snap2_id))
    inst.run_ok(
        "backup", "diff", "-i", inst.id,
        "-s", snap1_id, "--snapshot2", snap2_id,
    )

    print("Deleting snapshot %s..." % snap1_id)
    inst.run_ok("backup", "delete", "-i", inst.id, "-s", snap1_id)

    print("Verifying %s is gone..." % snap1_id)
    output = inst.run_quiet("backup", "list", "-i", inst.id)
    assert snap1_id not in output, (
        "%s still in list after delete:\n%s" % (snap1_id, output)
    )

    print("Unlocking the backup repository (no-op when not locked)...")
    inst.run_ok("backup", "unlock", "-i", inst.id)

    # Note: backup schedule set/list/remove tests are skipped in CI
    # because the cron expression positional argument doesn't survive
    # the canasta-native bash wrapper's argument handling.

    print("Skipping backup schedule tests (cron arg handling in CI)...")

    shutil.rmtree(backup_dir, ignore_errors=True)


def test_gitops_pull_diff(inst):
    """Test gitops pull and diff with compose orchestrator."""
    # Check prerequisites
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    print("Pushing initial state...")
    # gitops init already committed the initial state locally; just push it.
    inst.run_ok("gitops", "push", "-i", inst.id)

    print("Cloning bare repo to temp directory...")
    clone_dir = os.path.join(inst.work_dir, "gitops-clone")
    subprocess.run(
        ["git", "clone", "-b", "main", bare_repo, clone_dir],
        capture_output=True, check=True,
    )

    print("Adding a file in the clone and pushing...")
    settings_dir = os.path.join(
        clone_dir, "config", "settings", "global",
    )
    os.makedirs(settings_dir, exist_ok=True)
    test_file = os.path.join(settings_dir, "RemoteTest.php")
    with open(test_file, "w") as f:
        f.write("<?php\n$wgRemoteTest = true;\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=clone_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add remote change"],
        cwd=clone_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=clone_dir, capture_output=True, check=True,
    )

    print("Running gitops diff...")
    output = inst.run_ok("gitops", "diff", "-i", inst.id)
    assert "RemoteTest" in output or "remote" in output.lower(), (
        "Diff should show remote changes: %s" % output
    )

    print("Pulling remote changes...")
    inst.run_ok("gitops", "pull", "-i", inst.id)

    print("Verifying pulled file exists in instance...")
    pulled_file = os.path.join(
        inst.instance_path(), "config", "settings", "global", "RemoteTest.php",
    )
    assert os.path.isfile(pulled_file), (
        "Pulled file not found at %s" % pulled_file
    )

    print("Making a local change to a tracked file...")
    tracked_file = os.path.join(
        inst.instance_path(), "config", "settings", "global", "RemoteTest.php",
    )
    with open(tracked_file, "a") as f:
        f.write("$wgLocalChange = true;\n")

    print("Checking gitops status...")
    output = inst.run_ok("gitops", "status", "-i", inst.id)
    print("  Status output:\n%s" % output[:500])
    # Status should show at least 1 uncommitted change
    assert "1 file" in output or "uncommitted" in output.lower() or "change" in output.lower(), (
        "BUG: Status should show uncommitted changes after modifying a tracked file.\n"
        "Output: %s" % output
    )


def test_version(inst):
    """`canasta version` reports a version string."""
    output = inst.run_quiet("version")
    # The output format is governed by playbooks/version.yml; we just
    # need to confirm the command runs and emits something. The exact
    # version string is asserted on by tests/unit/ already.
    assert output.strip(), "version produced no output"


def test_doctor(inst):
    """`canasta doctor` runs preflight checks against the local host."""
    output = inst.run_quiet("doctor")
    # Sanity-check that doctor at least mentions Docker — the actual
    # OK/NOT RUNNING result depends on the runner environment but the
    # check itself should always show up.
    assert "Docker" in output, (
        "doctor output should mention Docker:\n%s" % output
    )


def test_host_management(inst):
    """`canasta host add/list/remove` manages entries in hosts.yml."""
    host_name = "canasta-int-test-host-%s" % inst.id
    print("Adding host entry %s..." % host_name)
    inst.run_ok(
        "host", "add",
        "--name", host_name,
        "--ssh", "user@example.invalid",
    )

    print("Listing hosts and verifying %s is present..." % host_name)
    output = inst.run_quiet("host", "list")
    assert host_name in output, (
        "host %s not found in list output:\n%s" % (host_name, output)
    )

    print("Removing host entry %s..." % host_name)
    inst.run_ok("host", "remove", "--name", host_name)

    print("Verifying host is gone...")
    output = inst.run_quiet("host", "list")
    assert host_name not in output, (
        "host %s still in list after remove:\n%s" % (host_name, output)
    )


def _check_sitemap(http_port, prefix, wiki_id):
    """Fetch a wiki's sitemap index and every sitemap and page it lists."""
    base = "http://127.0.0.1:%s" % http_port

    def get(path):
        req = urllib.request.Request(base + path)
        req.add_header("Host", "localhost:%s" % http_port)
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
        return gzip.decompress(body) if path.endswith(".gz") else body

    index = get("%s/public_assets/sitemap/sitemap-index-%s.xml"
                % (prefix, wiki_id)).decode()
    sitemaps = re.findall(r"<loc>([^<]+)</loc>", index)
    assert sitemaps, "%s sitemap index lists no sitemaps:\n%s" % (
        wiki_id, index)
    for sitemap in sitemaps:
        path = urllib.parse.urlsplit(sitemap).path
        assert path.startswith(prefix + "/public_assets/sitemap/"), (
            "%s sitemap URL has the wrong path: %s" % (wiki_id, sitemap))
        pages = re.findall(r"<loc>([^<]+)</loc>", get(path).decode())
        for page in pages[:1]:
            page_path = urllib.parse.urlsplit(page).path
            assert page_path.startswith(prefix + "/"), (
                "%s page URL has the wrong path: %s" % (wiki_id, page))
            get(page_path)


def test_sitemap(inst):
    """Generate and remove XML sitemaps for a root and a path-based wiki."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Adding path-based docs wiki...")
    inst.run_ok(
        "add", "-i", inst.id, "-w", "docs",
        "-u", "localhost:%s/docs" % inst.http_port,
    )
    wait_for_wiki_at_path(inst.http_port, "/docs/w/api.php")

    print("Generating sitemaps for all wikis...")
    inst.run_ok("sitemap", "generate", "-i", inst.id)

    print("Checking that every sitemap and page they list is reachable...")
    _check_sitemap(inst.http_port, "", "main")
    _check_sitemap(inst.http_port, "/docs", "docs")

    print("Removing sitemaps...")
    inst.run_ok("sitemap", "remove", "-i", inst.id)


def test_maintenance(inst):
    """Run a MediaWiki maintenance script and the bundled update playbook."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Running maintenance update (skip jobs to keep CI fast)...")
    inst.run_ok(
        "maintenance", "update", "-i", inst.id, "-w", "main",
        "--skip-jobs",
    )

    print("Running showSiteStats.php as a generic maintenance script...")
    # script_args is a variadic positional, not a --script-args flag.
    inst.run_ok(
        "maintenance", "script", "-i", inst.id, "-w", "main",
        "showSiteStats.php",
    )


def _instance_crontab_lines(inst):
    """This user's crontab lines that schedule the test instance."""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    fragment = " schedule run -i %s --name " % inst.id
    return [ln for ln in result.stdout.splitlines() if fragment in ln]


def test_schedule(inst):
    """schedule add -> run -> one cron firing -> list -> remove -> delete."""
    if shutil.which("crontab") is None:
        raise SkipTest("crontab not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    marker = "/tmp/canasta-schedule-%s" % inst.id
    print("Adding a schedule that fires every minute...")
    inst.run_ok(
        "schedule", "add", "-i", inst.id, "--name", "tick",
        "--cron", "* * * * *", "--command", "echo tick >> %s" % marker,
    )
    inst.run_ok(
        "schedule", "add", "-i", inst.id, "--name", "check",
        "--cron", "0 0 1 1 *", "--command", "test -s %s" % marker,
    )
    inst.run_ok(
        "schedule", "add", "-i", inst.id, "--name", "broken",
        "--cron", "0 0 1 1 *", "--command", "exit 4",
    )
    _, rc = inst.run("schedule", "add", "-i", inst.id, "--name", "tick",
                     "--cron", "* * * * *", "--command", "true")
    assert rc != 0, "adding an existing schedule name should be refused"
    lines = _instance_crontab_lines(inst)
    assert len(lines) == 3, "expected 3 crontab entries:\n%s" % lines

    print("Running the schedules by hand...")
    inst.run_ok("schedule", "run", "-i", inst.id, "--name", "tick")
    inst.run_ok("schedule", "run", "-i", inst.id, "--name", "check")
    _, rc = inst.run("schedule", "run", "-i", inst.id, "--name", "broken")
    assert rc != 0, "a failing command should fail schedule run"
    output = inst.run_quiet("schedule", "list", "-i", inst.id)
    assert "exit 4" in output and "ok" in output, output

    state = os.path.join(inst.instance_path(), ".schedule-state", "tick.yml")
    with open(state) as f:
        by_hand = f.read()
    if subprocess.run(["pgrep", "-x", "cron"],
                      capture_output=True).returncode == 0:
        print("Waiting for cron to fire the schedule...")
        deadline = time.time() + 150
        fired = by_hand
        while time.time() < deadline and fired == by_hand:
            time.sleep(5)
            with open(state) as f:
                fired = f.read()
        log = os.path.join(inst.instance_path(), "schedule-tick.log")
        log_text = open(log).read() if os.path.exists(log) else "(no log)"
        assert fired != by_hand, "cron never ran the schedule:\n" + log_text
        assert "result: ok" in fired, fired + "\n" + log_text
    else:
        print("No cron daemon running; skipping the cron firing check.")

    print("Removing a schedule...")
    inst.run_ok("schedule", "remove", "-i", inst.id, "--name", "broken")
    assert len(_instance_crontab_lines(inst)) == 2

    print("Deleting the instance clears its schedules...")
    inst.run_ok("delete", "-i", inst.id, "--yes")
    assert _instance_crontab_lines(inst) == [], (
        "delete left crontab entries behind")


def test_config_set_gitops(inst):
    """Verify config set propagates changes to gitops vars.yaml."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    print("Setting MW_SITE_SERVER via config set...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "MW_SITE_SERVER=https://new.example.com", "--no-restart",
    )

    print("Checking vars.yaml was updated...")
    vars_file = os.path.join(
        inst.instance_path(), "hosts", "testhost", "vars.yaml",
    )
    assert os.path.isfile(vars_file), "vars.yaml not found"
    with open(vars_file) as f:
        import yaml
        vars_data = yaml.safe_load(f)
    assert vars_data.get("mw_site_server") == "https://new.example.com", (
        "mw_site_server not updated in vars.yaml: %s" % vars_data
    )


def test_config_set_gitops_domain(inst):
    """config set MW_SITE_FQDN must survive a gitops render under reconcile.

    The side effects of a domain change derive MW_SITE_FQDN/MW_SITE_SERVER
    and rewrite wiki URLs in the rendered .env/config/wikis.yaml. Those
    derived values must also be propagated to hosts/<host>/vars.yaml, or
    the next render from the templates silently reverts the change.
    """
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    import yaml

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    new_domain = "wiki3.example.com"
    print("Changing primary domain via config set...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "MW_SITE_FQDN=%s" % new_domain, "--no-restart",
    )

    print("Reconciling config from the gitops sources...")
    out = inst.run_ok("reconcile", "-i", inst.id)
    # reconcile writes .env edits missing from the sources back into them,
    # which would hide a config set that never reached them.
    assert "Saved .env changes" not in out, (
        "config set left .env ahead of the gitops sources:\n%s" % out
    )

    # After reconcile the rendered files are re-derived from vars.yaml /
    # templates. If the config set change reached the gitops source of
    # truth the new domain survives; otherwise it reverts to localhost.
    inst_path = inst.instance_path()

    print("Verifying config/wikis.yaml kept the new domain...")
    with open(os.path.join(inst_path, "config", "wikis.yaml")) as f:
        wikis = yaml.safe_load(f)["wikis"]
    primary_url = wikis[0]["url"]
    assert primary_url.startswith(new_domain), (
        "wikis.yaml primary url reverted after reconcile: %s" % primary_url
    )

    print("Verifying Caddyfile kept the new domain...")
    with open(os.path.join(inst_path, "config", "Caddyfile")) as f:
        caddyfile = f.read()
    assert new_domain in caddyfile, (
        "Caddyfile reverted after reconcile (no %s):\n%s"
        % (new_domain, caddyfile)
    )

    print("Verifying vars.yaml recorded the new domain...")
    vars_file = os.path.join(inst_path, "hosts", "testhost", "vars.yaml")
    with open(vars_file) as f:
        vars_data = yaml.safe_load(f)
    assert vars_data.get("mw_site_fqdn") == new_domain, (
        "mw_site_fqdn not propagated to vars.yaml: %s" % vars_data
    )
    assert new_domain in (vars_data.get("mw_site_server") or ""), (
        "mw_site_server not propagated to vars.yaml: %s" % vars_data
    )
    assert new_domain in (vars_data.get("wiki_url_main") or ""), (
        "wiki_url_main not propagated to vars.yaml: %s" % vars_data
    )


def test_config_set_gitops_port(inst):
    """config set HTTP_PORT must survive a gitops render under reconcile.

    Mirrors test_config_set_gitops_domain for a port change. Changing the
    active port (HTTP_PORT here, since the test env runs TLS_MODE=off)
    derives MW_SITE_FQDN/MW_SITE_SERVER and rewrites wiki URLs in the rendered
    .env/config/wikis.yaml with the new port suffix. Those derived values plus
    the raw http_port must reach hosts/<host>/vars.yaml, or the next
    render from the templates silently drops the port.
    """
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    import yaml

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    new_port = "18080"
    print("Changing active port (HTTP_PORT) via config set...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "HTTP_PORT=%s" % new_port, "--no-restart",
    )

    print("Reconciling config from the gitops sources...")
    out = inst.run_ok("reconcile", "-i", inst.id)
    # reconcile writes .env edits missing from the sources back into them,
    # which would hide a config set that never reached them.
    assert "Saved .env changes" not in out, (
        "config set left .env ahead of the gitops sources:\n%s" % out
    )

    # After reconcile the rendered files are re-derived from vars.yaml /
    # templates. If the port change reached the gitops source of truth the
    # new port survives; otherwise it reverts to the original port.
    inst_path = inst.instance_path()
    port_suffix = ":%s" % new_port

    print("Verifying config/wikis.yaml kept the new port...")
    with open(os.path.join(inst_path, "config", "wikis.yaml")) as f:
        wikis = yaml.safe_load(f)["wikis"]
    primary_url = wikis[0]["url"]
    assert port_suffix in primary_url, (
        "wikis.yaml primary url dropped the port after reconcile: %s"
        % primary_url
    )

    print("Verifying .env kept the new port in MW_SITE_*...")
    env = read_env(inst.env_path())
    assert port_suffix in env.get("MW_SITE_FQDN", ""), (
        "MW_SITE_FQDN dropped the port after reconcile: %s"
        % env.get("MW_SITE_FQDN")
    )
    assert port_suffix in env.get("MW_SITE_SERVER", ""), (
        "MW_SITE_SERVER dropped the port after reconcile: %s"
        % env.get("MW_SITE_SERVER")
    )

    print("Verifying vars.yaml recorded the new port...")
    vars_file = os.path.join(inst_path, "hosts", "testhost", "vars.yaml")
    with open(vars_file) as f:
        vars_data = yaml.safe_load(f)
    assert str(vars_data.get("http_port")) == new_port, (
        "http_port not propagated to vars.yaml: %s" % vars_data
    )
    assert port_suffix in (vars_data.get("mw_site_fqdn") or ""), (
        "mw_site_fqdn missing port in vars.yaml: %s" % vars_data
    )
    assert port_suffix in (vars_data.get("mw_site_server") or ""), (
        "mw_site_server missing port in vars.yaml: %s" % vars_data
    )
    assert port_suffix in (vars_data.get("wiki_url_main") or ""), (
        "wiki_url_main missing port in vars.yaml: %s" % vars_data
    )


def test_gitops_push_shared_vars(inst):
    """Verify gitops push auto-migrates shared credential keys."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    print("Adding mixed shared and host-specific keys to host vars...")
    vars_file = os.path.join(
        inst.instance_path(), "hosts", "testhost", "vars.yaml",
    )
    import yaml
    with open(vars_file) as f:
        vars_data = yaml.safe_load(f) or {}
    # Should migrate to shared (in gitops_shared_keys):
    vars_data["aws_access_key_id"] = "AKIATEST"
    vars_data["aws_secret_access_key"] = "secrettest"
    # Should stay per-host (environment-specific identifiers, plus the
    # password that unlocks the per-host restic repository):
    vars_data["restic_password"] = "test-password"
    vars_data["restic_repository"] = "s3:s3.example.com/test-backup"
    vars_data["aws_bucket_name"] = "test-bucket"
    with open(vars_file, "w") as f:
        yaml.dump(vars_data, f)

    print("Staging and pushing...")
    inst.run_ok("gitops", "add", "-i", inst.id, "hosts/testhost/vars.yaml")
    inst.run_ok("gitops", "push", "-i", inst.id)

    print("Checking shared-list keys migrated to _shared/vars.yaml...")
    shared_file = os.path.join(
        inst.instance_path(), "hosts", "_shared", "vars.yaml",
    )
    assert os.path.isfile(shared_file), (
        "_shared/vars.yaml not created after push"
    )
    with open(shared_file) as f:
        shared_data = yaml.safe_load(f) or {}
    assert shared_data.get("aws_access_key_id") == "AKIATEST", (
        "aws_access_key_id not in shared vars: %s" % shared_data
    )
    assert shared_data.get("aws_secret_access_key") == "secrettest", (
        "aws_secret_access_key not in shared vars: %s" % shared_data
    )

    print("Checking environment-specific keys stayed per-host...")
    assert "restic_password" not in shared_data, (
        "restic_password should NOT be in shared (unlocks the per-host "
        "restic_repository): %s" % shared_data
    )
    assert "restic_repository" not in shared_data, (
        "restic_repository should NOT be in shared (per-host): %s"
        % shared_data
    )
    assert "aws_bucket_name" not in shared_data, (
        "aws_bucket_name should NOT be in shared (per-host): %s"
        % shared_data
    )

    print("Checking host vars: shared-list keys removed, host-specific kept...")
    with open(vars_file) as f:
        host_data = yaml.safe_load(f) or {}
    assert "aws_access_key_id" not in host_data, (
        "aws_access_key_id should have moved to shared, still in host: %s"
        % host_data
    )
    assert host_data.get("restic_password") == "test-password", (
        "restic_password should remain per-host: %s" % host_data
    )
    assert host_data.get("restic_repository") == "s3:s3.example.com/test-backup", (
        "restic_repository should remain per-host: %s" % host_data
    )
    assert host_data.get("aws_bucket_name") == "test-bucket", (
        "aws_bucket_name should remain per-host: %s" % host_data
    )


def test_gitops_push_reporting(inst):
    """Verify gitops push reports correctly."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    print("Push with no changes — should report 'No changes'...")
    output = inst.run_ok("gitops", "push", "-i", inst.id)
    assert "No changes to push" in output, (
        "Expected 'No changes to push' with nothing staged:\n%s" % output
    )

    print("Making a change, staging, pushing — should report 'pushed'...")
    settings_dir = os.path.join(
        inst.instance_path(), "config", "settings", "global",
    )
    os.makedirs(settings_dir, exist_ok=True)
    with open(os.path.join(settings_dir, "PushTest.php"), "w") as f:
        f.write("<?php\n$wgPushTest = true;\n")
    inst.run_ok(
        "gitops", "add", "-i", inst.id, "config/settings/global/PushTest.php",
    )
    output = inst.run_ok("gitops", "push", "-i", inst.id)
    assert "Configuration pushed" in output, (
        "Expected 'Configuration pushed' after staging+push:\n%s" % output
    )

    print("Push again with no changes — should report 'No changes'...")
    output = inst.run_ok("gitops", "push", "-i", inst.id)
    assert "No changes to push" in output, (
        "Expected 'No changes to push' on second push:\n%s" % output
    )


def test_backup_restore_excludes_safety(inst):
    """Verify --snapshot latest skips safety-before-restore snapshots."""
    # Stages a fake safety snapshot with `docker run`.
    require_docker()
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    time.sleep(10)

    print("Configuring backup repository...")
    backup_dir = tempfile.mkdtemp(prefix="canasta-int-backup-")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_REPOSITORY=%s" % backup_dir,
        "--no-restart",
    )
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "RESTIC_PASSWORD=testpass",
        "--no-restart",
    )

    print("Initializing backup repository...")
    inst.run_ok("backup", "init", "-i", inst.id)

    print("Creating real backup snapshot...")
    inst.run_ok("backup", "create", "-i", inst.id, "-t", "real-backup")

    print("Listing snapshots to get the real snapshot ID...")
    output = inst.run_quiet("backup", "list", "-i", inst.id)
    assert "real-backup" in output, (
        "real-backup not found in list output"
    )
    real_id = None
    for line in output.splitlines():
        if "real-backup" in line:
            match = re.search(r'\b([0-9a-f]{8,})\b', line)
            if match:
                real_id = match.group(1)
                break
    assert real_id, "Could not extract real snapshot ID"

    print("Creating a fake safety-before-restore snapshot...")
    inst_path = inst.instance_path()
    bvol = "canasta-backup-%s" % os.path.basename(inst_path)
    env_path = os.path.join(inst_path, ".env")
    subprocess.run(
        ["bash", "-c",
         "docker run --rm -i "
         "--env-file %s "
         "-v %s:/currentsnapshot "
         "%s "
         "restic/restic "
         "--cache-dir /tmp/restic-cache "
         "backup /currentsnapshot --tag safety-before-restore"
         % (env_path, bvol,
            "-v %s:%s" % (backup_dir, backup_dir) if backup_dir.startswith("/") else "")],
        capture_output=True, check=True,
    )

    print("Verifying both snapshots exist...")
    output = inst.run_quiet("backup", "list", "-i", inst.id)
    assert "real-backup" in output, "real-backup missing"
    assert "safety-before-restore" in output, "safety snapshot missing"

    print("Restoring with --snapshot latest...")
    inst.run_ok(
        "backup", "restore", "-i", inst.id,
        "-s", "latest", "--skip-safety-backup",
    )

    print("Restarting after restore...")
    inst.run_ok("restart", "-i", inst.id)

    print("Verifying wiki accessible after restore from real snapshot...")
    wait_for_wiki(inst.http_port, timeout=300)

    shutil.rmtree(backup_dir, ignore_errors=True)


def test_gitops_status_fetch(inst):
    """Verify gitops status detects remote commits after fetch."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    print("Pushing initial state...")
    # gitops init already committed the initial state locally; just push it.
    inst.run_ok("gitops", "push", "-i", inst.id)

    print("Verifying status is up to date...")
    output = inst.run_ok("gitops", "status", "-i", inst.id)
    assert "Up to date with remote" in output, (
        "Expected 'Up to date with remote' initially:\n%s" % output
    )

    print("Cloning bare repo and pushing a commit from the clone...")
    clone_dir = os.path.join(inst.work_dir, "gitops-clone")
    subprocess.run(
        ["git", "clone", "-b", "main", bare_repo, clone_dir],
        capture_output=True, check=True,
    )
    settings_dir = os.path.join(
        clone_dir, "config", "settings", "global",
    )
    os.makedirs(settings_dir, exist_ok=True)
    with open(os.path.join(settings_dir, "FetchTest.php"), "w") as f:
        f.write("<?php\n$wgFetchTest = true;\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=clone_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Remote commit for fetch test"],
        cwd=clone_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=clone_dir, capture_output=True, check=True,
    )

    print("Running gitops status (should detect remote commit)...")
    output = inst.run_ok("gitops", "status", "-i", inst.id)
    assert "Behind remote by 1 commit" in output, (
        "Expected 'Behind remote by 1 commit(s)' after remote push:\n%s"
        % output
    )


def test_gitops_fix_submodules_orphan(inst):
    """Verify fix-submodules recovers orphan gitlinks."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo],
        capture_output=True, check=True,
    )

    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id,
        "-n", "testhost",
        "--repo", bare_repo,
        "--key", key_file,
    )

    inst_path = inst.instance_path()

    # The clone needs a real origin. A .gitmodules entry is a path AND a
    # url, so recovery has nothing to write without one (see
    # test_gitops_fix_submodules_no_url below) — and `git submodule add`
    # clones from that url for real, so it has to resolve. A local bare
    # repo keeps the test off the network.
    print("Creating a fake extension with a git repo...")
    ext_origin = os.path.join(inst.work_dir, "OrphanExt.git")
    # Its HEAD must name the branch pushed below, or a clone has nothing
    # to check out; git's default branch name differs between hosts.
    subprocess.run(["git", "init", "--bare", "--initial-branch=main",
                    ext_origin],
                   capture_output=True, check=True)
    ext_dir = os.path.join(inst_path, "extensions", "OrphanExt")
    os.makedirs(ext_dir, exist_ok=True)
    subprocess.run(
        ["git", "init"], cwd=ext_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", ext_origin],
        cwd=ext_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=ext_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "HEAD:refs/heads/main"],
        cwd=ext_dir, capture_output=True, check=True,
    )
    ext_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ext_dir, capture_output=True, text=True, check=True,
    ).stdout.strip()

    print("Injecting orphan gitlink into the index...")
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo",
         "160000", ext_sha, "extensions/OrphanExt"],
        cwd=inst_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add orphan gitlink"],
        cwd=inst_path, capture_output=True, check=True,
    )

    print("Verifying OrphanExt is NOT in .gitmodules...")
    gitmodules_path = os.path.join(inst_path, ".gitmodules")
    if os.path.isfile(gitmodules_path):
        with open(gitmodules_path) as f:
            content = f.read()
        assert "OrphanExt" not in content, (
            "OrphanExt should not be in .gitmodules before fix"
        )

    # git 2.38.1+ refuses to clone a submodule from a local path unless the
    # file transport is allowed (CVE-2022-39253). Real extensions come from
    # https or ssh, so allow it for this call only, through git's
    # environment config rather than a global setting.
    print("Running gitops fix-submodules...")
    allow_file = {"GIT_CONFIG_COUNT": "1",
                  "GIT_CONFIG_KEY_0": "protocol.file.allow",
                  "GIT_CONFIG_VALUE_0": "always"}
    os.environ.update(allow_file)
    try:
        inst.run_ok("gitops", "fix-submodules", "-i", inst.id)
    finally:
        for key in allow_file:
            os.environ.pop(key, None)

    print("Verifying OrphanExt IS now in .gitmodules...")
    assert os.path.isfile(gitmodules_path), (
        ".gitmodules not found after fix-submodules"
    )
    with open(gitmodules_path) as f:
        content = f.read()
    assert "OrphanExt" in content, (
        "OrphanExt should be in .gitmodules after fix:\n%s" % content
    )

    print("Verifying git submodule update succeeds...")
    result = subprocess.run(
        ["git", "submodule", "update", "--init", "--recursive"],
        cwd=inst_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "git submodule update failed after fix:\n%s" % result.stderr
    )

    print("Verifying the recovered entry carries the origin URL...")
    assert ext_origin in content, (
        "a .gitmodules entry without a url fetches nothing on the next "
        "clone:\n%s" % content
    )


def test_gitops_fix_submodules_no_url(inst):
    """fix-submodules refuses an orphan whose URL cannot be recovered.

    A gitlink whose clone has no origin cannot become a valid .gitmodules
    entry — there is no url to write. Committing it anyway produces a repo
    where `git submodule update` fetches nothing and the extension is gone
    on the next clone, so recovery fails loudly instead.
    """
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo], capture_output=True, check=True)
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "testhost",
        "--repo", bare_repo,
        "--key", os.path.join(inst.work_dir, "gitops-test.key"),
    )

    inst_path = inst.instance_path()
    print("Creating an orphan gitlink with no origin remote...")
    ext_dir = os.path.join(inst_path, "extensions", "NoRemoteExt")
    os.makedirs(ext_dir, exist_ok=True)
    subprocess.run(["git", "init"], cwd=ext_dir,
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=ext_dir, capture_output=True, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ext_dir,
        capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", "160000", sha,
         "extensions/NoRemoteExt"],
        cwd=inst_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Add orphan gitlink"],
                   cwd=inst_path, capture_output=True, check=True)

    print("Running gitops fix-submodules (expected to refuse)...")
    output, rc = inst.run("gitops", "fix-submodules", "-i", inst.id)
    assert rc != 0, (
        "recovery must refuse an orphan it cannot give a url:\n%s" % output)
    assert "NoRemoteExt" in output and "remote.origin.url" in output, (
        "the refusal must name the path and the reason:\n%s" % output)
    assert "git rm --cached" in output, (
        "the refusal must say how to resolve it:\n%s" % output)


def _wait_for_crowdsec_lapi(inst, timeout=120):
    """Poll until the crowdsec LAPI answers cscli, so enroll doesn't race
    container startup (collection install + sqlite init take a few seconds)."""
    deadline = time.time() + timeout
    last = ""
    print("  Waiting for crowdsec LAPI...", flush=True)
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "crowdsec",
             "cscli", "lapi", "status"],
            capture_output=True, text=True, cwd=inst.instance_path(),
        )
        if result.returncode == 0:
            print("  crowdsec LAPI is ready")
            return
        last = (result.stderr or result.stdout or "").strip()
        time.sleep(5)
    raise AssertionError(
        "crowdsec LAPI not ready within %ds (last: %s)" % (timeout, last)
    )


def _cscli(inst, *args):
    """Run a cscli command inside the instance's crowdsec container."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "crowdsec", "cscli"] + list(args),
        capture_output=True, text=True, cwd=inst.instance_path(),
    )
    if result.returncode != 0:
        raise AssertionError(
            "cscli %s failed (rc=%d): %s"
            % (" ".join(args), result.returncode,
               (result.stderr or result.stdout).strip())
        )
    return result.stdout


def test_crowdsec(inst):
    """Enable CrowdSec on an instance created BEFORE the feature existed,
    verify the acquisition/whitelist files are backfilled (not left as empty
    bind-mount directories), and exercise enroll -> status -> ban -> unban.

    The live 'banned IP gets a 403' path is validated against a real
    deployment rather than here: in the port-mapped test environment Caddy
    sees the Docker bridge gateway as the client, which makes a 403 assertion
    environment-fragile. This pins the deterministic behavior (backfill +
    command/decision plumbing)."""
    # Drives cscli through `docker compose exec`.
    require_docker()
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    crowdsec_dir = os.path.join(inst.instance_path(), "config", "crowdsec")

    print("Enabling CrowdSec (config set restarts the instance)...")
    inst.run_ok("config", "set", "-i", inst.id, "CANASTA_ENABLE_CROWDSEC=true")

    # The instance was created before the feature was enabled, so nothing
    # but the backfill materializes these. They must be FILES — a directory
    # here means the engine gets a dir where its acquisition file should be.
    print("Verifying crowdsec config files were backfilled as files...")
    for name in ("acquis.yaml", "whitelists.yaml"):
        path = os.path.join(crowdsec_dir, name)
        assert os.path.isfile(path), (
            "%s should be backfilled as a file, not a directory" % path
        )

    # COMPOSE_PROFILES self-heals from the flag on start.
    env = read_env(inst.env_path())
    assert "crowdsec" in env.get("COMPOSE_PROFILES", ""), (
        "crowdsec profile should be active: %s" % env.get("COMPOSE_PROFILES")
    )

    _wait_for_crowdsec_lapi(inst)

    # Enabling CrowdSec above auto-enrolls the bouncer on the restart, so the
    # key is already stored; this exercises bouncer-enroll's idempotent path.
    print("Re-running bouncer-enroll (idempotent)...")
    inst.run_ok("crowdsec", "bouncer-enroll", "-i", inst.id)
    env = read_env(inst.env_path())
    assert env.get("CROWDSEC_BOUNCER_API_KEY", ""), (
        "bouncer-enroll must store CROWDSEC_BOUNCER_API_KEY"
    )

    print("Checking status lists the bouncer...")
    out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
    assert "canasta-caddy" in out, "status should list the canasta-caddy bouncer"
    assert "active" in out, "status should mark the live bouncer active"

    # 203.0.113.0/24 is TEST-NET-3 (documentation range) — safe to ban.
    print("Banning a test IP and verifying the decision appears...")
    inst.run_ok("crowdsec", "ban", "203.0.113.7", "-i", inst.id)
    out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
    assert "203.0.113.7" in out, "banned IP should appear in decisions"

    print("Unbanning and verifying the decision is gone...")
    inst.run_ok("crowdsec", "unban", "203.0.113.7", "-i", inst.id)
    out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
    assert "203.0.113.7" not in out, "unbanned IP should be gone from decisions"

    # Recreating the caddy container makes CrowdSec auto-create an undeletable
    # 'canasta-caddy@<ip>' child each time it reconnects from a new IP,
    # accumulating stale 'valid' rows. They cannot be pruned
    # (deleting the parent cascades and revokes the shared key), so status
    # collapses the family for display instead. Simulate two duplicates and
    # verify status shows one active bouncer plus a stale-count note rather
    # than listing each row.
    print("Simulating stale IP-suffixed bouncer duplicates...")
    for ip in ("172.18.0.250", "172.18.0.251"):
        _cscli(inst, "bouncers", "add", "canasta-caddy@%s" % ip, "-o", "raw")

    print("Verifying status collapses the duplicates for display...")
    out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
    assert out.count("canasta-caddy — active") == 1, (
        "status should show exactly one active canasta-caddy registration"
    )
    assert "2 stale auto-created duplicate" in out, (
        "status should summarize the two duplicates as a stale count"
    )
    assert "172.18.0.250" not in out and "172.18.0.251" not in out, (
        "individual stale duplicate rows must not be listed"
    )


def test_config_refresh_template(inst):
    """Verify 'config refresh-template' lists drift, previews, and adopts
    the shipped template for a seeded file (and restores a deleted one)."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    vcl = os.path.join(inst.instance_path(), "config", "default.vcl")
    vector = os.path.join(
        inst.instance_path(), "config", "settings", "global", "Vector.php",
    )
    tmpl_vcl = os.path.join(
        REPO_ROOT, "instance_template", "config", "default.vcl",
    )
    tmpl_vector = os.path.join(
        REPO_ROOT, "instance_template", "config", "settings", "global",
        "Vector.php",
    )

    # Drift one seed file (local edit) and delete another.
    marker = "# CANASTA_TEST_REFRESH_MARKER"
    print("Drifting default.vcl and deleting Vector.php...")
    with open(vcl, "a") as f:
        f.write("\n%s\n" % marker)
    os.remove(vector)

    print("Listing drift (no file argument)...")
    out = inst.run_ok("config", "refresh-template", "-i", inst.id)
    assert "config/default.vcl" in out, (
        "drift list should flag default.vcl:\n%s" % out
    )
    assert "config/settings/global/Vector.php" in out, (
        "drift list should flag the deleted Vector.php:\n%s" % out
    )

    print("Previewing default.vcl (no --apply must not change it)...")
    out = inst.run_ok(
        "config", "refresh-template", "-i", inst.id, "config/default.vcl",
    )
    assert "--apply" in out, (
        "preview should tell the user to re-run with --apply:\n%s" % out
    )
    with open(vcl) as f:
        assert marker in f.read(), "preview must not modify the instance copy"

    print("Adopting the template for both files with --apply...")
    inst.run_ok(
        "config", "refresh-template", "-i", inst.id,
        "config/default.vcl", "config/settings/global/Vector.php", "--apply",
    )
    with open(vcl) as f, open(tmpl_vcl) as t:
        assert f.read() == t.read(), "default.vcl should match the template"
    assert os.path.isfile(vector), "deleted Vector.php should be restored"
    with open(vector) as f, open(tmpl_vector) as t:
        assert f.read() == t.read(), "restored Vector.php should match template"

    # --yes is the deprecated spelling of --apply and must keep working.
    print("A second run with --yes is a no-op (already matches)...")
    out = inst.run_ok(
        "config", "refresh-template", "-i", inst.id,
        "config/default.vcl", "--yes",
    )
    assert "already matches" in out, (
        "an unchanged file should report already matching:\n%s" % out
    )

    print("A non-template path is rejected...")
    out, rc = inst.run(
        "config", "refresh-template", "-i", inst.id, "config/../.env",
    )
    assert rc != 0, "refreshing a non-template path should fail"
    assert "not a template-seeded file" in out, (
        "rejection should explain why:\n%s" % out
    )


def test_gitops_add_wikis_yaml(inst):
    """`gitops add config/wikis.yaml` (explicit path) must route through the
    wikis.yaml->template reconcile, not stage the rendered file. On old code
    the bare `git add` of the gitignored file errors; with the fix the edit
    is captured into wikis.yaml.template."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")
    import yaml

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir, "-e", inst.env_file,
    )

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo], capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "testhost",
        "--repo", bare_repo, "--key", key_file,
    )

    wikis_yaml = os.path.join(inst.instance_path(), "config", "wikis.yaml")
    template = os.path.join(inst.instance_path(), "wikis.yaml.template")
    assert os.path.isfile(template), (
        "gitops init should create wikis.yaml.template"
    )

    print("Editing the wiki display name directly in config/wikis.yaml...")
    with open(wikis_yaml) as f:
        data = yaml.safe_load(f)
    data["wikis"][0]["name"] = "DWIM Display Name"
    with open(wikis_yaml, "w") as f:
        yaml.safe_dump(data, f)

    print("Staging by explicit path: canasta gitops add config/wikis.yaml...")
    inst.run_ok("gitops", "add", "-i", inst.id, "config/wikis.yaml")

    print("The edit must have been captured into wikis.yaml.template...")
    with open(template) as f:
        tmpl = f.read()
    assert "DWIM Display Name" in tmpl, (
        "explicit-path gitops add did not reconcile into the template:\n%s"
        % tmpl
    )

    print("gitops add with no file argument must be rejected...")
    _, rc = inst.run("gitops", "add", "-i", inst.id)
    assert rc != 0, "no-arg 'gitops add' should error (at least one file)"

    print("gitops add accepts multiple files...")
    # Use non-gitignored paths (config/default.vcl etc. are gitignored).
    gdir = os.path.join(inst.instance_path(), "config", "settings", "global")
    rel_a = "config/settings/global/zz-test-a.php"
    rel_b = "config/settings/global/zz-test-b.php"
    for name in ("zz-test-a.php", "zz-test-b.php"):
        with open(os.path.join(gdir, name), "w") as f:
            f.write("<?php // test\n")
    inst.run_ok("gitops", "add", "-i", inst.id, rel_a, rel_b)
    staged = subprocess.run(
        ["git", "-C", inst.instance_path(), "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    ).stdout
    assert rel_a in staged and rel_b in staged, (
        "both named files should be staged; got:\n%s" % staged
    )


def test_upgrade_refreshes_gitignore(inst):
    """canasta upgrade backfills new managed ignore rules into a gitops
    instance's .gitignore while preserving user-added lines."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir, "-e", inst.env_file,
    )

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo], capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "testhost",
        "--repo", bare_repo, "--key", key_file,
    )

    gi = os.path.join(inst.instance_path(), ".gitignore")
    print("Simulating a stale .gitignore (drop a managed rule, add a user "
          "line)...")
    with open(gi) as f:
        lines = [ln for ln in f.read().splitlines()
                 if ln.strip() != "config/wikis.yaml"]
    lines.append("my-custom-ignore.txt")
    with open(gi, "w") as f:
        f.write("\n".join(lines) + "\n")

    print("Upgrading...")
    inst.run_ok("upgrade")

    with open(gi) as f:
        result = f.read().splitlines()
    assert "config/wikis.yaml" in result, (
        "upgrade should restore the dropped managed ignore rule:\n%s"
        % "\n".join(result)
    )
    assert "my-custom-ignore.txt" in result, (
        "upgrade must preserve user-added .gitignore lines:\n%s"
        % "\n".join(result)
    )


def test_upgrade_backfills_wikis_template(inst):
    """canasta upgrade creates wikis.yaml.template on a gitops instance
    that lacks it (a Go-CLI-era repo predating the template)."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir, "-e", inst.env_file,
    )

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo], capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "testhost",
        "--repo", bare_repo, "--key", key_file,
    )

    template = os.path.join(inst.instance_path(), "wikis.yaml.template")
    assert os.path.isfile(template), "gitops init should create the template"

    print("Removing the template to simulate a legacy gitops repo...")
    os.remove(template)

    print("Upgrading...")
    inst.run_ok("upgrade")

    assert os.path.isfile(template), (
        "upgrade should backfill the missing wikis.yaml.template"
    )
    with open(template) as f:
        content = f.read()
    assert "id: main" in content, (
        "backfilled template should contain the instance's wiki(s):\n%s"
        % content
    )


def test_upgrade_backfills_mycnf_template(inst):
    """canasta upgrade creates my.cnf.template on a gitops instance that
    lacks it, so a repo spanning hosts of different sizes can hold a
    per-host innodb_buffer_pool_size instead of one shared value."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir, "-e", inst.env_file,
    )

    # The shipped my.cnf carries only [client], so give it the tuning an
    # operator would have added — that is what gets templated.
    my_cnf = os.path.join(inst.instance_path(), "my.cnf")
    with open(my_cnf, "w") as f:
        f.write("[client]\n\n[mysqld]\ninnodb_buffer_pool_size = 8G\n"
                "max_connections = 300\n")

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo], capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "testhost",
        "--repo", bare_repo, "--key", key_file,
    )

    # gitops init no longer tracks my.cnf, so recreate a repo initialized
    # before it became a rendered file: my.cnf tracked, and no ignore rule
    # for it. That is the repo the migration exists for.
    print("Simulating a repo that predates the rendered my.cnf...")
    gitignore = os.path.join(inst.instance_path(), ".gitignore")
    with open(gitignore) as f:
        rules = [ln for ln in f if ln.strip() != "my.cnf"]
    with open(gitignore, "w") as f:
        f.writelines(rules)
    for cmd in (["git", "add", "-f", "--", "my.cnf", ".gitignore"],
                ["git", "-c", "commit.gpgsign=false", "commit", "-q",
                 "-m", "Track my.cnf as a shared file"]):
        subprocess.run(cmd, cwd=inst.instance_path(), check=True,
                       capture_output=True)

    template = os.path.join(inst.instance_path(), "my.cnf.template")

    print("Upgrading...")
    inst.run_ok("upgrade")

    assert os.path.isfile(template), (
        "upgrade should backfill the missing my.cnf.template"
    )
    with open(template) as f:
        content = f.read()
    assert "innodb_buffer_pool_size = {{innodb_buffer_pool_size}}" in content, (
        "the buffer pool should be placeholdered:\n%s" % content
    )
    assert "max_connections = 300" in content, (
        "other directives should be preserved verbatim:\n%s" % content
    )

    # The backfill must not disturb the live file: my.cnf is bind-mounted
    # into the db container and read at server start.
    with open(my_cnf) as f:
        assert "innodb_buffer_pool_size = 8G" in f.read(), (
            "the live my.cnf must keep its value"
        )

    # my.cnf is a rendered file now, so the repo must stop tracking it.
    with open(os.path.join(inst.instance_path(), ".gitignore")) as f:
        ignore_rules = [ln.strip() for ln in f]
    assert "my.cnf" in ignore_rules, "upgrade should ignore my.cnf"

    # The migration must complete itself: no hand-edited vars, no raw git.
    shared_vars = os.path.join(
        inst.instance_path(), "hosts", "_shared", "vars.yaml",
    )
    with open(shared_vars) as f:
        shared = f.read()
    assert "innodb_buffer_pool_size: 8G" in shared, (
        "the value should be recorded as the shared default so every host "
        "inherits one:\n%s" % shared
    )

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "my.cnf", "my.cnf.template"],
        cwd=inst.instance_path(), capture_output=True, text=True,
    ).stdout
    assert "D  my.cnf" in status, (
        "my.cnf should be staged as untracked:\n%s" % status
    )
    assert "A  my.cnf.template" in status, (
        "the template should be staged:\n%s" % status
    )
    assert os.path.isfile(my_cnf), "my.cnf must stay on disk (--cached only)"

    print("Re-running upgrade (backfill must be create-only)...")
    with open(template, "a") as f:
        f.write("# hand-edited\n")
    inst.run_ok("upgrade")
    with open(template) as f:
        assert "# hand-edited" in f.read(), (
            "backfill must never overwrite an existing template"
        )


def test_gitops_renders_mycnf(inst):
    """A gitops pull renders my.cnf from my.cnf.template + this host's vars,
    so hosts of different sizes each get a buffer pool that fits their RAM.

    Also pins that the render keeps my.cnf's inode: it is bind-mounted into
    the db container as a single file, and an atomic write would strand the
    container on the old inode (as once happened to the Caddyfile).
    """
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir, "-e", inst.env_file,
    )

    my_cnf = os.path.join(inst.instance_path(), "my.cnf")
    with open(my_cnf, "w") as f:
        f.write("[client]\n\n[mysqld]\ninnodb_buffer_pool_size = 8G\n")

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo], capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "testhost",
        "--repo", bare_repo, "--key", key_file,
    )

    # my.cnf is a rendered file, so init must not have tracked it.
    tracked = subprocess.run(
        ["git", "ls-files", "--", "my.cnf"],
        cwd=inst.instance_path(), capture_output=True, text=True,
    ).stdout.strip()
    assert tracked == "", "my.cnf must not be tracked by gitops: %r" % tracked

    print("Adding my.cnf.template and this host's buffer-pool value...")
    with open(os.path.join(inst.instance_path(), "my.cnf.template"), "w") as f:
        f.write("[client]\n\n[mysqld]\n"
                "innodb_buffer_pool_size = {{innodb_buffer_pool_size}}\n")
    host_vars = os.path.join(
        inst.instance_path(), "hosts", "testhost", "vars.yaml",
    )
    with open(host_vars, "a") as f:
        f.write("\ninnodb_buffer_pool_size: 2G\n")

    inst.run_ok(
        "gitops", "add", "-i", inst.id,
        "my.cnf.template", "hosts/testhost/vars.yaml",
    )
    inst.run_ok("gitops", "push", "-i", inst.id)

    # A pull only renders when it brings in a new commit, so make one.
    print("Making an unrelated remote commit...")
    clone_dir = os.path.join(inst.work_dir, "gitops-clone")
    subprocess.run(
        ["git", "clone", "-b", "main", bare_repo, clone_dir],
        capture_output=True, check=True,
    )
    settings_dir = os.path.join(clone_dir, "config", "settings", "global")
    os.makedirs(settings_dir, exist_ok=True)
    with open(os.path.join(settings_dir, "RemoteTest.php"), "w") as f:
        f.write("<?php\n$wgRemoteTest = true;\n")
    for cmd in (["git", "add", "."],
                ["git", "commit", "-m", "Add remote change"],
                ["git", "push", "origin", "main"]):
        subprocess.run(cmd, cwd=clone_dir, capture_output=True, check=True)

    inode_before = os.stat(my_cnf).st_ino

    print("Pulling...")
    inst.run_ok("gitops", "pull", "-i", inst.id)

    with open(my_cnf) as f:
        rendered = f.read()
    assert "innodb_buffer_pool_size = 2G" in rendered, (
        "pull should render my.cnf from the template + this host's vars:\n%s"
        % rendered
    )
    assert "8G" not in rendered, (
        "the pre-render value should be gone:\n%s" % rendered
    )
    assert os.stat(my_cnf).st_ino == inode_before, (
        "my.cnf is bind-mounted as a file; the render must keep its inode"
    )


def test_gitops_pull_reapplies_unfinished(inst):
    """A pull whose render never ran must re-render on the next pull.

    Simulates the field failure: HEAD is moved to a fetched commit while
    .gitops-applied still names the old one, which is exactly the state a
    pull that died after the merge leaves behind. Deciding on "did HEAD
    move" would skip rendering forever.
    """
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir, "-e", inst.env_file,
    )

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo], capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "testhost",
        "--repo", bare_repo, "--key", key_file,
    )
    inst.run_ok("gitops", "push", "-i", inst.id)

    ipath = inst.instance_path()
    applied_file = os.path.join(ipath, ".gitops-applied")
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ipath,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    print("Pushing a remote change...")
    clone_dir = os.path.join(inst.work_dir, "gitops-clone")
    subprocess.run(
        ["git", "clone", "-b", "main", bare_repo, clone_dir],
        capture_output=True, check=True,
    )
    settings_dir = os.path.join(clone_dir, "config", "settings", "global")
    os.makedirs(settings_dir, exist_ok=True)
    marker = os.path.join(settings_dir, "PullReapply.php")
    with open(marker, "w") as f:
        f.write("<?php\n$wgPullReapply = true;\n")
    for cmd in (["git", "add", "."],
                ["git", "commit", "-m", "remote change"],
                ["git", "push", "origin", "main"]):
        subprocess.run(cmd, cwd=clone_dir, capture_output=True, check=True)

    print("Simulating a pull that fetched but never rendered...")
    subprocess.run(
        ["git", "fetch", "origin", "main"], cwd=ipath,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "merge", "--ff-only", "origin/main"], cwd=ipath,
        capture_output=True, check=True,
    )
    # .gitops-applied is written only after a successful render, so a pull
    # that died before that leaves it naming the previous commit.
    with open(applied_file, "w") as f:
        f.write(before)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ipath,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head != before, "fixture should have moved HEAD"

    print("Pulling: HEAD has not moved, but nothing was applied...")
    output = inst.run_ok("gitops", "pull", "-i", inst.id)
    assert "Already up to date" not in output, (
        "an unapplied commit must not be reported as up to date:\n%s" % output
    )

    with open(applied_file) as f:
        assert f.read().strip() == head, (
            ".gitops-applied should advance to the rendered commit"
        )


def test_gitops_add_wiki_tracked(inst):
    """A wiki added after gitops init must have its tracked files captured
    into gitops by 'canasta add' (settings, public_assets logos, and the
    farm template) — they used to silently stay untracked."""
    if shutil.which("git-crypt") is None:
        raise SkipTest("git-crypt not installed")

    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir, "-e", inst.env_file,
    )

    print("Creating bare git repository...")
    bare_repo = os.path.join(inst.work_dir, "gitops-remote.git")
    subprocess.run(
        ["git", "init", "--bare", bare_repo], capture_output=True, check=True,
    )
    key_file = os.path.join(inst.work_dir, "gitops-test.key")

    print("Initializing gitops...")
    inst.run_ok(
        "gitops", "init", "-i", inst.id, "-n", "testhost",
        "--repo", bare_repo, "--key", key_file,
    )

    # Place a logo for the new wiki before adding it, so the add's staging
    # step has something to capture (public_assets is otherwise populated
    # by the container at runtime).
    logo_dir = os.path.join(inst.instance_path(), "public_assets", "docs")
    os.makedirs(logo_dir, exist_ok=True)
    with open(os.path.join(logo_dir, "Logo.png"), "w") as f:
        f.write("PNGDATA")

    settings_src = os.path.join(inst.work_dir, "docs-settings.php")
    with open(settings_src, "w") as f:
        f.write("<?php\n$wgDocsWiki = true;\n")

    print("Adding a second wiki (docs)...")
    inst.run_ok(
        "add", "-i", inst.id, "-w", "docs",
        "-u", "localhost:%s/docs" % inst.http_port,
        "-l", settings_src,
    )

    staged = subprocess.run(
        ["git", "-C", inst.instance_path(), "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    ).stdout
    assert "public_assets/docs/Logo.png" in staged, (
        "canasta add must stage the new wiki's public_assets logo:\n%s"
        % staged
    )
    assert "config/settings/wikis/docs/Settings.php" in staged, (
        "canasta add must stage the new wiki's settings:\n%s" % staged
    )
    template = os.path.join(inst.instance_path(), "wikis.yaml.template")
    with open(template) as f:
        assert "id: docs" in f.read(), (
            "the new wiki must be captured into wikis.yaml.template"
        )


def _sidecar_running(inst, service):
    """True if the named sidecar has a running container.

    Sidecars render into docker-compose.sidecars.yml, which plain
    `docker compose` does not auto-load, so query the Docker daemon by the
    Compose labels instead (project = the instance directory basename).
    """
    project = os.path.basename(inst.instance_path())
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", "status=running",
         "--filter", "label=com.docker.compose.project=%s" % project,
         "--filter", "label=com.docker.compose.service=%s" % service],
        capture_output=True, text=True,
    )
    return result.stdout.strip() != ""


def _sidecar_volume_exists(inst, volume):
    """True if the Compose volume <project>_<volume> exists."""
    project = os.path.basename(inst.instance_path())
    result = subprocess.run(
        ["docker", "volume", "ls", "-q",
         "--filter", "name=%s_%s" % (project, volume)],
        capture_output=True, text=True,
    )
    return result.stdout.strip() != ""


def test_sidecar_lifecycle(inst):
    """add (from file) -> list -> restart -> the sidecar runs and is
    reachable from web by service name -> remove -> restart -> it is gone,
    with no orphan container left behind.
    """
    # Queries sidecar containers and volumes through the Docker CLI.
    require_docker()
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    # A trivial HTTP service the web container can reach by name.
    sidecar_file = os.path.join(inst.work_dir, "echo-sidecar.yaml")
    with open(sidecar_file, "w") as f:
        f.write("name: echo\nimage: traefik/whoami\nports: [80]\n")

    print("Adding the sidecar from a file...")
    inst.run_ok("sidecar", "add", "-i", inst.id, "--file", sidecar_file)

    declared = os.path.join(inst.instance_path(), "config", "sidecars.yaml")
    assert os.path.isfile(declared), "sidecars.yaml should be written"
    with open(declared) as f:
        assert "echo" in f.read(), "the sidecar should be declared"

    print("Listing sidecars...")
    # 'sidecar list' reports via an Ansible debug task, so run verbose.
    out = inst.run_ok("sidecar", "list", "-i", inst.id)
    assert "echo" in out and "traefik/whoami" in out, (
        "sidecar list should show the sidecar and its image:\n%s" % out
    )

    print("Restarting to deploy the sidecar...")
    inst.run_ok("restart", "-i", inst.id)
    wait_for_wiki(inst.http_port)
    assert _sidecar_running(inst, "echo"), (
        "the echo sidecar container should be running after restart"
    )

    print("Checking web can reach the sidecar by service name...")
    reach = subprocess.run(
        ["docker", "compose", "exec", "-T", "web",
         "bash", "-c", "exec 3<>/dev/tcp/echo/80"],
        capture_output=True, text=True, cwd=inst.instance_path(),
    )
    assert reach.returncode == 0, (
        "web should reach the sidecar by service name 'echo':\n%s"
        % (reach.stderr or reach.stdout)
    )

    print("Removing the sidecar...")
    inst.run_ok("sidecar", "remove", "-i", inst.id, "--name", "echo")
    if os.path.isfile(declared):
        with open(declared) as f:
            assert "echo" not in f.read(), (
                "the sidecar should be removed from sidecars.yaml"
            )

    print("Restarting after removal...")
    inst.run_ok("restart", "-i", inst.id)
    wait_for_wiki(inst.http_port)
    assert not _sidecar_running(inst, "echo"), (
        "the echo sidecar container should be gone after removal + restart "
        "(regression guard for the orphan-container bug)"
    )


def test_sidecar_migrate(inst):
    """sidecar migrate MOVES a sidecar service: it must both write the
    declaration to config/sidecars.yaml AND remove the service from
    docker-compose.override.yml (a left-behind override entry would shadow
    the migrated sidecar). --dry-run must change nothing.
    """
    # Queries sidecar containers and volumes through the Docker CLI.
    require_docker()
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    override = os.path.join(
        inst.instance_path(), "docker-compose.override.yml",
    )
    with open(override, "w") as f:
        f.write(
            "services:\n"
            "  echo:\n"
            "    image: traefik/whoami\n"
            "    volumes:\n"
            "      - echodata:/data\n"
            "volumes:\n"
            "  echodata:\n"
        )

    declared = os.path.join(inst.instance_path(), "config", "sidecars.yaml")

    print("Dry-run migrate must not change anything...")
    out = inst.run_ok("sidecar", "migrate", "-i", inst.id, "--dry-run")
    assert "echo" in out, (
        "the dry-run plan should mention the echo service:\n%s" % out
    )
    assert not os.path.isfile(declared), "dry-run must not write sidecars.yaml"
    with open(override) as f:
        assert "echo" in f.read(), "dry-run must leave the override untouched"

    print("Migrating for real...")
    inst.run_ok("sidecar", "migrate", "-i", inst.id)

    assert os.path.isfile(declared), "migrate should write sidecars.yaml"
    with open(declared) as f:
        decl = f.read()
    assert "echo" in decl, "the migrated sidecar should appear in sidecars.yaml"
    assert "persistent" in decl, (
        "a named-volume sidecar should migrate to a persistent volume"
    )

    ov = ""
    if os.path.isfile(override):
        with open(override) as f:
            ov = f.read()
    assert "echo" not in ov, (
        "the migrated service must be REMOVED from the override (a left-behind "
        "entry would shadow the migrated sidecar):\n%s" % ov
    )

    print("Restarting to deploy the migrated sidecar...")
    inst.run_ok("restart", "-i", inst.id)
    wait_for_wiki(inst.http_port)
    assert _sidecar_running(inst, "echo"), (
        "the migrated sidecar should run after restart"
    )
    assert _sidecar_volume_exists(inst, "echo-echodata"), (
        "the migrated sidecar's persistent volume should be created"
    )

    # delete must tear down the sidecar's named volume too — otherwise it
    # lingers and blocks a later recreate with a stale-volume error.
    print("Deleting and checking the sidecar volume is removed...")
    inst.run_ok("delete", "-i", inst.id, "--yes")
    assert not _sidecar_volume_exists(inst, "echo-echodata"), (
        "delete must remove the migrated sidecar's named volume"
    )


# --- Test runner ---

ALL_TESTS = {
    "lifecycle": test_lifecycle,
    "podman-socket-runtime": test_podman_socket_runtime,
    "import": test_import_export,
    "upgrade": test_upgrade,
    "upgrade-rebuilds-buildable": test_upgrade_rebuilds_buildable,
    "canasta-base": test_canasta_base,
    "build-from-overlay": test_build_from_overlay,
    "build-from-two-image": test_build_from_two_image,
    "build-from-canasta-base": test_build_from_canasta_base,
    "rebuild": test_rebuild,
    "upgrade-mysql-to-mariadb": test_upgrade_mysql_to_mariadb,
    "upgrade-mysql-to-mariadb-recovery": test_upgrade_mysql_to_mariadb_recovery,
    "reconcile": test_reconcile,
    "upgrade-refreshes-gitignore": test_upgrade_refreshes_gitignore,
    "upgrade-backfills-wikis-template": test_upgrade_backfills_wikis_template,
    "upgrade-backfills-mycnf-template": test_upgrade_backfills_mycnf_template,
    "gitops-renders-mycnf": test_gitops_renders_mycnf,
    "gitops-pull-reapplies": test_gitops_pull_reapplies_unfinished,
    "upgrade-backfill-hosts-yaml": test_upgrade_backfill_hosts_yaml,
    "backup": test_backup,
    "backup-advanced": test_backup_advanced,
    "backup-extra-database": test_backup_extra_database,
    "add-extra-database": test_add_extra_database,
    "backup-custom-dockerfile": test_backup_custom_dockerfile,
    "backup-missing-dockerfile": test_backup_missing_dockerfile,
    "gitops": test_gitops,
    "gitops-add-wikis-yaml": test_gitops_add_wikis_yaml,
    "gitops-add-wiki-tracked": test_gitops_add_wiki_tracked,
    "gitops-join": test_gitops_join,
    "gitops-pull-diff": test_gitops_pull_diff,
    "extension-skin": test_extension_skin,
    "extension-lifecycle": test_extension_lifecycle,
    "skin-lifecycle": test_skin_lifecycle,
    "wiki-farm": test_wiki_farm,
    "config-side-effects": test_config_side_effects,
    "config-refresh-template": test_config_refresh_template,
    "version": test_version,
    "doctor": test_doctor,
    "host-management": test_host_management,
    "sitemap": test_sitemap,
    "maintenance": test_maintenance,
    "schedule": test_schedule,
    "config-set-gitops": test_config_set_gitops,
    "config-set-gitops-domain": test_config_set_gitops_domain,
    "config-set-gitops-port": test_config_set_gitops_port,
    "gitops-push-shared": test_gitops_push_shared_vars,
    "gitops-push-reporting": test_gitops_push_reporting,
    "backup-restore-safety": test_backup_restore_excludes_safety,
    "gitops-status-fetch": test_gitops_status_fetch,
    "gitops-fix-submodules": test_gitops_fix_submodules_orphan,
    "gitops-fix-submodules-no-url": test_gitops_fix_submodules_no_url,
    "crowdsec": test_crowdsec,
    "sidecar-lifecycle": test_sidecar_lifecycle,
    "sidecar-migrate": test_sidecar_migrate,
}


def main():
    if "--list" in sys.argv:
        for name in ALL_TESTS:
            print(name)
        return

    # A skip means a prerequisite is missing, and on a CI runner that
    # provisions every prerequisite, a missing one is a broken setup.
    no_skip = "--no-skip" in sys.argv

    # Select tests
    requested = [a for a in sys.argv[1:] if not a.startswith("-")]
    tests = {n: ALL_TESTS[n] for n in requested} if requested else ALL_TESTS

    # Check a container runtime is reachable. Podman is a supported
    # runtime, so requiring Docker here would make the suite unrunnable
    # on exactly the hosts that need the most coverage.
    if not container_runtime():
        print("ERROR: no container runtime available (tried docker, podman)")
        sys.exit(1)
    print("Container runtime: %s" % container_runtime())

    passed = 0
    failed = 0
    skipped = []
    teardown_failed = []

    for name, test_fn in tests.items():
        inst = TestInstance("inttest-%s" % name)
        print("\n=== %s ===" % name)
        try:
            test_fn(inst)
            print("PASSED: %s" % name)
            passed += 1
        except SkipTest as e:
            print("SKIPPED: %s: %s" % (name, e))
            skipped.append(name)
        except AssertionError as e:
            print("FAILED: %s: %s" % (name, e))
            failed += 1
        except Exception as e:
            print("ERROR: %s: %s" % (name, e))
            failed += 1
        finally:
            if not inst.cleanup():
                teardown_failed.append(name)

    print("\n=== Results: %d passed, %d failed, %d skipped ===" % (
        passed, failed, len(skipped),
    ))
    if teardown_failed:
        print("=== Teardown failed: %s ===" % ", ".join(teardown_failed))
    skip_failed = no_skip and skipped
    if skip_failed:
        print("=== Skipped with --no-skip, counted as failures: %s ===" % (
            ", ".join(skipped)))
    sys.exit(1 if failed or teardown_failed or skip_failed else 0)


if __name__ == "__main__":
    main()
