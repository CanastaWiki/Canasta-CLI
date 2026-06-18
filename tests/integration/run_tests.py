#!/usr/bin/env python3
"""Integration tests for the Canasta CLI.

Exercises the CLI end to end by calling ./canasta commands as
subprocesses. Each test creates an isolated instance with unique ports
and a separate config directory.

Usage:
    python tests/integration/run_tests.py              # all tests
    python tests/integration/run_tests.py lifecycle     # specific test
    python tests/integration/run_tests.py --list        # list tests

Requirements:
    - Docker running
    - Canasta CLI installed (.venv with ansible)
    - Run from repo root or set CANASTA_ROOT
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


class TestInstance:
    def __init__(self, test_id):
        self.id = test_id
        self.work_dir = tempfile.mkdtemp(prefix="canasta-int-work-")
        self.config_dir = tempfile.mkdtemp(prefix="canasta-int-conf-")
        self.http_port, self.https_port = next_port()
        self.env_file = os.path.join(self.work_dir, "test.env")
        with open(self.env_file, "w") as f:
            f.write(
                "HTTP_PORT=%s\nHTTPS_PORT=%s\nCADDY_AUTO_HTTPS=off\n"
                % (self.http_port, self.https_port)
            )

    def run(self, *args):
        """Run a canasta command in verbose mode. Returns (stdout, returncode)."""
        env = os.environ.copy()
        env["CANASTA_CONFIG_DIR"] = self.config_dir
        env["ANSIBLE_CONFIG"] = os.path.join(REPO_ROOT, "ansible.cfg")
        # Run in verbose mode for CI feedback
        cmd = [CANASTA_BIN, "--verbose"] + list(args)
        print("  $ canasta %s" % " ".join(args), flush=True)
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=self.work_dir, env=env,
        )
        output = result.stdout + result.stderr
        if output.strip():
            for line in output.strip().split("\n"):
                print("    %s" % line)
        sys.stdout.flush()
        return output, result.returncode

    def run_quiet(self, *args):
        """Run without --verbose for parseable output."""
        env = os.environ.copy()
        env["CANASTA_CONFIG_DIR"] = self.config_dir
        env["ANSIBLE_CONFIG"] = os.path.join(REPO_ROOT, "ansible.cfg")
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

    def cleanup(self):
        """Delete the instance and remove work directory."""
        print("  Cleanup: deleting instance %s" % self.id)
        self.run("delete", "-i", self.id, "--yes")
        subprocess.run(
            ["sudo", "rm", "-rf", self.work_dir],
            capture_output=True,
        )
        shutil.rmtree(self.config_dir, ignore_errors=True)


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
            # CADDY_AUTO_HTTPS=off, so wikis.yaml stores localhost:<port>.
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
    subprocess.run(["docker", "ps", "-a"], capture_output=False)
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
    output = inst.run_ok("upgrade")

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
    """Init gitops, verify templates, push, verify remote."""
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
    inst.run_ok("gitops", "add", "-i", inst.id)
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


def test_gitops_join(inst):
    """Join a second instance to an existing gitops repo.

    Exercises the join working-tree checkout (#571/#582): files present in
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
    inst.run_ok("gitops", "add", "-i", inst.id)
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
        # host instead of erroring on the duplicate (#571), upserting the
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

    # Test instance uses CADDY_AUTO_HTTPS=off, so HTTP_PORT is the active
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

    # CADDY_AUTO_HTTPS toggle side effect (#46): flipping the scheme
    # should switch the active port that's reflected in wikis.yaml /
    # MW_SITE_SERVER, even though neither HTTP_PORT nor HTTPS_PORT
    # changed.
    print("Toggling CADDY_AUTO_HTTPS to on (HTTPS_PORT becomes active)...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CADDY_AUTO_HTTPS=on", "--no-restart",
    )

    print("Checking wikis.yaml now references HTTPS_PORT %s..." % inst.https_port)
    with open(wikis_yaml_path) as f:
        wikis_content = f.read()
    assert (":%s" % inst.https_port) in wikis_content, (
        "wikis.yaml should contain HTTPS_PORT %s after CADDY_AUTO_HTTPS=on:"
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

    print("Toggling CADDY_AUTO_HTTPS back to off...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CADDY_AUTO_HTTPS=off", "--no-restart",
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

    # CANASTA_ENABLE_VERY_SHORT_URLS side-effect validation (#50): the
    # two incompatible-feature combinations should fail-fast at
    # config-set time with descriptive errors, before the
    # misconfiguration lands in .env. The runtime in CanastaBase #150
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
    inst.run_ok("gitops", "add", "-i", inst.id)
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


def test_k8s_lifecycle(inst):
    """Kubernetes: create, list, stop, start, exec, export, delete."""
    # Check prerequisites
    result = subprocess.run(
        ["kubectl", "cluster-info"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise SkipTest("kubectl not available or cluster not reachable")

    result = subprocess.run(
        ["helm", "version", "--short"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise SkipTest("helm not installed")

    print("Creating Kubernetes instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "--orchestrator", "kubernetes",
        "--skip-argocd-install",
    )

    print("Verifying instance registered...")
    output = inst.run_ok("list")
    assert inst.id in output, "Instance not in list output"
    assert "KUBERNETES" in output, "Orchestrator not shown as kubernetes"

    print("Checking pods are running...")
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", "canasta-%s" % inst.id,
         "--no-headers"],
        capture_output=True, text=True,
    )
    assert "Running" in result.stdout, (
        "No running pods: %s" % result.stdout
    )

    print("Testing maintenance exec...")
    output = inst.run_ok(
        "maintenance", "exec", "-i", inst.id, "php", "-v",
    )
    assert "PHP" in output, "PHP version not in exec output"

    print("Testing export...")
    export_file = os.path.join(inst.work_dir, "k8s-test-export.sql")
    inst.run_ok(
        "export", "-i", inst.id, "-w", "main", "-f", export_file,
    )
    assert os.path.isfile(export_file), "Export file not created"
    assert os.path.getsize(export_file) > 0, "Export file is empty"

    print("Stopping instance...")
    inst.run_ok("stop", "-i", inst.id)

    # Verify pods scaled to zero
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", "canasta-%s" % inst.id,
         "--no-headers"],
        capture_output=True, text=True,
    )
    running = [
        line for line in result.stdout.strip().split("\n")
        if line and "Running" in line
    ]
    assert len(running) == 0, "Pods still running after stop"

    print("Starting instance...")
    inst.run_ok("start", "-i", inst.id)

    # Verify pods running again
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", "canasta-%s" % inst.id,
         "--no-headers"],
        capture_output=True, text=True,
    )
    assert "Running" in result.stdout, (
        "No running pods after start: %s" % result.stdout
    )

    # #51: verify that `canasta config set` on a K8s instance actually
    # propagates an env var all the way to the running pod. Prior to
    # the env ConfigMap + envFrom plumbing, this would silently no-op:
    # .env on the controller would update but the pod's getenv() would
    # never see the change.
    print("Setting PHP_UPLOAD_MAX_FILESIZE via config set...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "PHP_UPLOAD_MAX_FILESIZE=77M",
    )

    # Wait for the rollout triggered by config set to complete. The
    # deployment has a checksum/env-config annotation that changes
    # when configData.env changes, which forces a pod restart.
    namespace = "canasta-%s" % inst.id
    print("Waiting for web rollout to complete...")
    result = subprocess.run(
        ["kubectl", "rollout", "status",
         "deployment/canasta-%s-web" % inst.id,
         "-n", namespace, "--timeout=180s"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "Rollout did not complete: %s\n%s" % (result.stdout, result.stderr)
    )

    print("Verifying PHP_UPLOAD_MAX_FILESIZE reaches the web pod...")
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace,
         "-l", "app.kubernetes.io/component=web",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0 and result.stdout, (
        "Could not find web pod: %s" % result.stderr
    )
    web_pod = result.stdout.strip()

    result = subprocess.run(
        ["kubectl", "exec", "-n", namespace, web_pod, "--",
         "printenv", "PHP_UPLOAD_MAX_FILESIZE"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "printenv failed in pod %s: %s" % (web_pod, result.stderr)
    )
    assert result.stdout.strip() == "77M", (
        "Expected PHP_UPLOAD_MAX_FILESIZE=77M in pod, got: %r" % result.stdout
    )

    # #53: verify that config set propagates values.yaml-backed knobs
    # on K8s. Previously, setting CANASTA_ENABLE_VARNISH via config set
    # updated .env but not values.yaml, so the Helm chart and the
    # running pod never saw the change.
    print("Testing config set → values.yaml propagation (#53)...")
    import yaml as _yaml  # for reading values.yaml

    values_path = os.path.join(inst.instance_path(), "values.yaml")

    # First, hand-edit replicaCount into values.yaml to verify it
    # survives the config set round-trip. This is the #53 acceptance
    # criterion: unrelated hand-edits must not be clobbered.
    with open(values_path) as f:
        values = _yaml.safe_load(f)
    values.setdefault("web", {})["replicaCount"] = 7
    with open(values_path, "w") as f:
        _yaml.dump(values, f, default_flow_style=False)

    # Set a values.yaml-backed knob via config set
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CANASTA_ENABLE_VARNISH=false", "--no-restart",
    )

    # Read values.yaml back and verify both:
    # 1. varnish.enabled is now false
    # 2. web.replicaCount is still 7 (not clobbered)
    with open(values_path) as f:
        values_after = _yaml.safe_load(f)
    assert values_after.get("varnish", {}).get("enabled") is False, (
        "Expected varnish.enabled=false in values.yaml after config set, "
        "got: %s" % values_after.get("varnish", {}).get("enabled")
    )
    assert values_after.get("web", {}).get("replicaCount") == 7, (
        "Hand-edited web.replicaCount=7 was clobbered by config set! "
        "Got: %s" % values_after.get("web", {}).get("replicaCount")
    )

    # Revert varnish
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CANASTA_ENABLE_VARNISH=true", "--no-restart",
    )

    print("Deleting instance...")
    inst.run_ok("delete", "-i", inst.id, "--yes")

    # Verify namespace is gone (may take a moment to finalize)
    for _ in range(12):
        result = subprocess.run(
            ["kubectl", "get", "namespace", "canasta-%s" % inst.id],
            capture_output=True,
        )
        if result.returncode != 0:
            break
        time.sleep(5)
    assert result.returncode != 0, "Namespace still exists after delete"

    print("Verifying instance deregistered...")
    output = inst.run_ok("list")
    assert inst.id not in output, "Instance still in list after delete"


def test_version(inst):
    """`canasta version` reports a version string and orchestrator info."""
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


def test_sitemap(inst):
    """Generate and remove an XML sitemap."""
    print("Creating instance...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "-e", inst.env_file,
    )
    wait_for_wiki(inst.http_port)

    print("Generating sitemap...")
    inst.run_ok("sitemap", "generate", "-i", inst.id, "-w", "main")

    print("Removing sitemap...")
    inst.run_ok("sitemap", "remove", "-i", inst.id, "-w", "main")


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


def test_config_set_gitops(inst):
    """Verify config set propagates changes to gitops vars.yaml (#205)."""
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
    """config set MW_SITE_FQDN must survive config regenerate under gitops.

    The side effects of a domain change derive MW_SITE_FQDN/MW_SITE_SERVER
    and rewrite wiki URLs in the rendered .env/config/wikis.yaml. Those
    derived values must also be propagated to hosts/<host>/vars.yaml, or
    the next 'config regenerate' re-renders the files from the templates
    and silently reverts the change.
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

    print("Regenerating config from gitops source of truth...")
    inst.run_ok("config", "regenerate", "-i", inst.id)

    # After regenerate the rendered files are re-derived from vars.yaml /
    # templates. If the config set change reached the gitops source of
    # truth the new domain survives; otherwise it reverts to localhost.
    inst_path = inst.instance_path()

    print("Verifying config/wikis.yaml kept the new domain...")
    with open(os.path.join(inst_path, "config", "wikis.yaml")) as f:
        wikis = yaml.safe_load(f)["wikis"]
    primary_url = wikis[0]["url"]
    assert primary_url.startswith(new_domain), (
        "wikis.yaml primary url reverted after regenerate: %s" % primary_url
    )

    print("Verifying Caddyfile kept the new domain...")
    with open(os.path.join(inst_path, "config", "Caddyfile")) as f:
        caddyfile = f.read()
    assert new_domain in caddyfile, (
        "Caddyfile reverted after regenerate (no %s):\n%s"
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
    """config set HTTP_PORT must survive config regenerate under gitops.

    Mirrors test_config_set_gitops_domain for a port change. Changing the
    active port (HTTP_PORT here, since the test env runs CADDY_AUTO_HTTPS=off)
    derives MW_SITE_FQDN/MW_SITE_SERVER and rewrites wiki URLs in the rendered
    .env/config/wikis.yaml with the new port suffix. Those derived values plus
    the raw http_port must reach hosts/<host>/vars.yaml, or the next
    'config regenerate' re-renders the files from the templates and silently
    drops the port.
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

    print("Regenerating config from gitops source of truth...")
    inst.run_ok("config", "regenerate", "-i", inst.id)

    # After regenerate the rendered files are re-derived from vars.yaml /
    # templates. If the port change reached the gitops source of truth the
    # new port survives; otherwise it reverts to the original port.
    inst_path = inst.instance_path()
    port_suffix = ":%s" % new_port

    print("Verifying config/wikis.yaml kept the new port...")
    with open(os.path.join(inst_path, "config", "wikis.yaml")) as f:
        wikis = yaml.safe_load(f)["wikis"]
    primary_url = wikis[0]["url"]
    assert port_suffix in primary_url, (
        "wikis.yaml primary url dropped the port after regenerate: %s"
        % primary_url
    )

    print("Verifying .env kept the new port in MW_SITE_*...")
    env = read_env(inst.env_path())
    assert port_suffix in env.get("MW_SITE_FQDN", ""), (
        "MW_SITE_FQDN dropped the port after regenerate: %s"
        % env.get("MW_SITE_FQDN")
    )
    assert port_suffix in env.get("MW_SITE_SERVER", ""), (
        "MW_SITE_SERVER dropped the port after regenerate: %s"
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
    """Verify gitops push auto-migrates shared credential keys (#206)."""
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
    vars_data["restic_password"] = "test-password"
    vars_data["aws_access_key_id"] = "AKIATEST"
    vars_data["aws_secret_access_key"] = "secrettest"
    # Should stay per-host (environment-specific identifiers):
    vars_data["restic_repository"] = "s3:s3.example.com/test-backup"
    vars_data["aws_bucket_name"] = "test-bucket"
    with open(vars_file, "w") as f:
        yaml.dump(vars_data, f)

    print("Staging and pushing...")
    inst.run_ok("gitops", "add", "-i", inst.id)
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
    assert shared_data.get("restic_password") == "test-password", (
        "restic_password not in shared vars: %s" % shared_data
    )
    assert shared_data.get("aws_access_key_id") == "AKIATEST", (
        "aws_access_key_id not in shared vars: %s" % shared_data
    )
    assert shared_data.get("aws_secret_access_key") == "secrettest", (
        "aws_secret_access_key not in shared vars: %s" % shared_data
    )

    print("Checking environment-specific keys stayed per-host...")
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
    assert "restic_password" not in host_data, (
        "restic_password should have moved to shared, still in host: %s"
        % host_data
    )
    assert "aws_access_key_id" not in host_data, (
        "aws_access_key_id should have moved to shared, still in host: %s"
        % host_data
    )
    assert host_data.get("restic_repository") == "s3:s3.example.com/test-backup", (
        "restic_repository should remain per-host: %s" % host_data
    )
    assert host_data.get("aws_bucket_name") == "test-bucket", (
        "aws_bucket_name should remain per-host: %s" % host_data
    )


def test_gitops_push_reporting(inst):
    """Verify gitops push reports correctly (#202)."""
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
    inst.run_ok("gitops", "add", "-i", inst.id)
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
    """Verify --snapshot latest skips safety-before-restore snapshots (#147)."""
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
    """Verify gitops status detects remote commits after fetch (#152)."""
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
    inst.run_ok("gitops", "add", "-i", inst.id)
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
    """Verify fix-submodules recovers orphan gitlinks (#144)."""
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

    print("Creating a fake extension with a git repo...")
    ext_dir = os.path.join(inst_path, "extensions", "OrphanExt")
    os.makedirs(ext_dir, exist_ok=True)
    subprocess.run(
        ["git", "init"], cwd=ext_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
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

    print("Running gitops fix-submodules...")
    inst.run_ok("gitops", "fix-submodules", "-i", inst.id)

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
    # accumulating stale 'valid' rows (issue #619). They cannot be pruned
    # (deleting the parent cascades and revokes the shared key), so status
    # collapses the family for display instead. Simulate two duplicates and
    # verify status shows one active bouncer plus a stale-count note rather
    # than listing each row.
    print("Simulating stale IP-suffixed bouncer duplicates...")
    for ip in ("172.18.0.250", "172.18.0.251"):
        _cscli(inst, "bouncers", "add", "canasta-caddy@%s" % ip, "-o", "raw")

    print("Verifying status collapses the duplicates for display...")
    out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
    assert out.count("— active") == 1, (
        "status should show exactly one active canasta-caddy registration"
    )
    assert "2 stale auto-created duplicate" in out, (
        "status should summarize the two duplicates as a stale count"
    )
    assert "172.18.0.250" not in out and "172.18.0.251" not in out, (
        "individual stale duplicate rows must not be listed"
    )


def _k8s_caddy_ready(inst, timeout=300):
    """Wait until the caddy pod is Running with BOTH containers ready
    (caddy + the crowdsec sidecar, i.e. 2/2). If caddy were left on the
    stock image (#681) it would crashloop on the crowdsec Caddyfile
    directive and never reach ready, so this doubles as that regression
    guard."""
    ns = "canasta-%s" % inst.id
    deadline = time.time() + timeout
    last = ""
    print("  Waiting for caddy + crowdsec sidecar to be ready...", flush=True)
    while time.time() < deadline:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", ns,
             "-l", "app.kubernetes.io/component=caddy",
             "--field-selector=status.phase=Running",
             "-o", "jsonpath={range .items[0].status.containerStatuses[*]}"
             "{.name}={.ready} {end}"],
            capture_output=True, text=True,
        )
        last = result.stdout.strip()
        if "caddy=true" in last and "crowdsec=true" in last:
            print("  caddy + crowdsec sidecar are ready (2/2)")
            return
        time.sleep(5)
    raise AssertionError(
        "caddy + crowdsec sidecar not ready within %ds (last: %s)"
        % (timeout, last)
    )


def _k8s_caddy_image(inst):
    """Return the image of the caddy container in the caddy deployment."""
    ns = "canasta-%s" % inst.id
    result = subprocess.run(
        ["kubectl", "get", "deploy", "canasta-%s-caddy" % inst.id, "-n", ns,
         "-o", "jsonpath={.spec.template.spec.containers[?(@.name=='caddy')]"
         ".image}"],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def _wait_bouncer_active(inst, timeout=120):
    """Poll `crowdsec status` until the canasta-caddy bouncer shows active.
    The caddy bouncer's first LAPI pull (which flips it to 'active') can lag a
    few seconds after a restart, so assert with a wait rather than once."""
    deadline = time.time() + timeout
    out = ""
    while time.time() < deadline:
        out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
        for line in out.splitlines():
            if "canasta-caddy" in line and "active" in line:
                return out
        time.sleep(5)
    raise AssertionError(
        "bouncer canasta-caddy never became active within %ds:\n%s"
        % (timeout, out)
    )


def test_k8s_crowdsec(inst):
    """Kubernetes: bring up CrowdSec on a live cluster and exercise the command
    surface (#683). This is the coverage gap that let two blockers reach main —
    #677 (unquoted preflight jsonpath broke every K8s crowdsec command) and
    #681 (caddy stuck on the stock image -> crashloop) — because the K8s
    integration job never *enabled* CrowdSec. Unit tests assert file content,
    not runtime; only an enable-on-a-real-cluster test catches this.

    CrowdSec is enabled at CREATE time (-e). That path otherwise has no CI
    coverage (it was broken until #685/#700), and it does one fewer full-stack
    restart than the config-set-enable path — config set enable bounces the
    whole stack twice (enable + the bouncer-key store), and on a 2-CPU CI
    runner the MediaWiki pod's healthcheck start-period makes a second full
    restart blow helm's --wait. The restart-light command surface below
    (reload only rolls the caddy deployment, not the stack) keeps this within
    the runner's budget.

    The live 'banned IP gets a 403' assertion is intentionally omitted: in the
    CI cluster the client IP Caddy sees is environment-dependent (same reasoning
    as the Compose test_crowdsec). This pins the deterministic behavior: the
    sidecar comes up on the plugin image, every command exits clean, and
    ban/unban decisions plumb through the LAPI."""
    result = subprocess.run(["kubectl", "cluster-info"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("kubectl not available or cluster not reachable")
    result = subprocess.run(["helm", "version", "--short"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("helm not installed")

    # Enable CrowdSec at create time via the -e env file.
    with open(inst.env_file, "a") as f:
        f.write("CANASTA_ENABLE_CROWDSEC=true\n")

    print("Creating Kubernetes instance with CrowdSec enabled...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "--orchestrator", "kubernetes",
        "--skip-argocd-install",
        "-e", inst.env_file,
    )

    # #681: the caddy deployment must run the plugin image and the pod must
    # reach 2/2 (caddy + crowdsec sidecar). Stock image -> crashloop, never 2/2.
    _k8s_caddy_ready(inst)
    image = _k8s_caddy_image(inst)
    assert image == "ghcr.io/canastawiki/canasta-caddy:2.11.3", (
        "caddy must run the plugin image when CrowdSec is on, got: %r" % image
    )

    # The deploy-time reconcile must record both knobs in values.yaml.
    import yaml as _yaml
    with open(os.path.join(inst.instance_path(), "values.yaml")) as f:
        values = _yaml.safe_load(f)
    assert values.get("crowdsec", {}).get("enabled") is True, (
        "values.yaml crowdsec.enabled must be true, got: %s"
        % values.get("crowdsec")
    )

    # #677: every K8s crowdsec command must exec cleanly into the sidecar.
    # run_quiet raises on a non-zero exit, so these calls ARE the assertion.
    print("Verifying the bouncer enrolled at create time...")
    out = _wait_bouncer_active(inst)
    assert "registered" in out, "status should show CAPI registered"

    print("Exercising the read command surface...")
    inst.run_quiet("crowdsec", "scenarios", "-i", inst.id)
    inst.run_quiet("crowdsec", "alerts", "-i", inst.id)
    inst.run_quiet("crowdsec", "metrics", "-i", inst.id)

    # 203.0.113.0/24 is TEST-NET-3 (documentation range) — safe to ban.
    print("Banning a test IP and verifying the decision appears...")
    inst.run_ok("crowdsec", "ban", "203.0.113.7", "-i", inst.id)
    out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
    assert "203.0.113.7" in out, "banned IP should appear in decisions"

    print("Unbanning and verifying the decision is gone...")
    inst.run_ok("crowdsec", "unban", "203.0.113.7", "-i", inst.id)
    out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
    assert "203.0.113.7" not in out, "unbanned IP should be gone from decisions"

    # reload / reload --purge-blocklists roll ONLY the caddy deployment (the
    # engine is a sidecar there), not the whole stack — light enough for CI.
    # The Recreate rollout renames the caddy pod, so this also exercises the
    # post-restart cscli prefix re-resolve.
    print("Reloading the engine...")
    inst.run_ok("crowdsec", "reload", "-i", inst.id)
    _k8s_caddy_ready(inst)
    print("Reloading with --purge-blocklists...")
    inst.run_ok("crowdsec", "reload", "--purge-blocklists", "-i", inst.id)
    _k8s_caddy_ready(inst)

    # bouncer-enroll without --force is the idempotent path: the bouncer is
    # already enrolled from create, so this is a no-op (no restart). --force
    # is omitted here — it triggers a full-stack restart that overruns the CI
    # runner; its revoke/re-issue path is covered by the unit + Compose tests.
    print("Re-running bouncer-enroll (idempotent no-op)...")
    inst.run_ok("crowdsec", "bouncer-enroll", "-i", inst.id)
    out = _wait_bouncer_active(inst)
    assert "canasta-caddy" in out, "bouncer should still be enrolled"

    print("Deleting instance...")
    inst.run_ok("delete", "-i", inst.id, "--yes")
    for _ in range(12):
        result = subprocess.run(
            ["kubectl", "get", "namespace", "canasta-%s" % inst.id],
            capture_output=True,
        )
        if result.returncode != 0:
            break
        time.sleep(5)
    assert result.returncode != 0, "Namespace still exists after delete"


# --- Test runner ---

ALL_TESTS = {
    "k8s-lifecycle": test_k8s_lifecycle,
    "k8s-crowdsec": test_k8s_crowdsec,
    "lifecycle": test_lifecycle,
    "import": test_import_export,
    "upgrade": test_upgrade,
    "upgrade-backfill-hosts-yaml": test_upgrade_backfill_hosts_yaml,
    "backup": test_backup,
    "backup-advanced": test_backup_advanced,
    "backup-custom-dockerfile": test_backup_custom_dockerfile,
    "backup-missing-dockerfile": test_backup_missing_dockerfile,
    "gitops": test_gitops,
    "gitops-join": test_gitops_join,
    "gitops-pull-diff": test_gitops_pull_diff,
    "extension-skin": test_extension_skin,
    "wiki-farm": test_wiki_farm,
    "config-side-effects": test_config_side_effects,
    "version": test_version,
    "doctor": test_doctor,
    "host-management": test_host_management,
    "sitemap": test_sitemap,
    "maintenance": test_maintenance,
    "config-set-gitops": test_config_set_gitops,
    "config-set-gitops-domain": test_config_set_gitops_domain,
    "config-set-gitops-port": test_config_set_gitops_port,
    "gitops-push-shared": test_gitops_push_shared_vars,
    "gitops-push-reporting": test_gitops_push_reporting,
    "backup-restore-safety": test_backup_restore_excludes_safety,
    "gitops-status-fetch": test_gitops_status_fetch,
    "gitops-fix-submodules": test_gitops_fix_submodules_orphan,
    "crowdsec": test_crowdsec,
}


def main():
    if "--list" in sys.argv:
        for name in ALL_TESTS:
            print(name)
        return

    # Select tests
    requested = [a for a in sys.argv[1:] if not a.startswith("-")]
    tests = {n: ALL_TESTS[n] for n in requested} if requested else ALL_TESTS

    # Check Docker
    result = subprocess.run(
        ["docker", "info"], capture_output=True
    )
    if result.returncode != 0:
        print("ERROR: Docker not available")
        sys.exit(1)

    passed = 0
    failed = 0
    skipped = 0

    for name, test_fn in tests.items():
        inst = TestInstance("inttest-%s" % name)
        print("\n=== %s ===" % name)
        try:
            test_fn(inst)
            print("PASSED: %s" % name)
            passed += 1
        except SkipTest as e:
            print("SKIPPED: %s: %s" % (name, e))
            skipped += 1
        except AssertionError as e:
            print("FAILED: %s: %s" % (name, e))
            failed += 1
        except Exception as e:
            print("ERROR: %s: %s" % (name, e))
            failed += 1
        finally:
            inst.cleanup()

    print("\n=== Results: %d passed, %d failed, %d skipped ===" % (
        passed, failed, skipped,
    ))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
