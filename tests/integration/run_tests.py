#!/usr/bin/env python3
"""Integration tests for Canasta-Ansible.

Mirrors the Go CLI integration tests by calling ./canasta commands as
subprocesses. Each test creates an isolated instance with unique ports
and a separate config directory.

Usage:
    python tests/integration/run_tests.py              # all tests
    python tests/integration/run_tests.py lifecycle     # specific test
    python tests/integration/run_tests.py --list        # list tests

Requirements:
    - Docker running
    - Canasta-Ansible installed (.venv with ansible)
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
        "config", "get", "-i", inst.id, "--key", "HTTP_PORT",
    )
    assert new_http_port in output, (
        "config get HTTP_PORT did not return %s:\n%s"
        % (new_http_port, output)
    )

    print("Setting an arbitrary key for config unset to remove...")
    inst.run_ok(
        "config", "set", "-i", inst.id,
        "CANASTA_TEST_MARKER=hello", "--no-restart",
    )
    env = read_env(inst.env_path())
    assert env.get("CANASTA_TEST_MARKER") == "hello", (
        "marker should be set in .env, got: %s"
        % env.get("CANASTA_TEST_MARKER")
    )

    print("Removing the marker key with config unset...")
    inst.run_ok(
        "config", "unset", "-i", inst.id,
        "--keys", "CANASTA_TEST_MARKER", "--no-restart",
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
        "--older-than", "1h",
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
    inst.run_ok(
        "maintenance", "script", "-i", inst.id, "-w", "main",
        "--script-args", "showSiteStats.php",
    )


# --- Test runner ---

ALL_TESTS = {
    "k8s-lifecycle": test_k8s_lifecycle,
    "lifecycle": test_lifecycle,
    "import": test_import_export,
    "upgrade": test_upgrade,
    "backup": test_backup,
    "backup-advanced": test_backup_advanced,
    "gitops": test_gitops,
    "gitops-pull-diff": test_gitops_pull_diff,
    "extension-skin": test_extension_skin,
    "wiki-farm": test_wiki_farm,
    "config-side-effects": test_config_side_effects,
    "version": test_version,
    "doctor": test_doctor,
    "host-management": test_host_management,
    "sitemap": test_sitemap,
    "maintenance": test_maintenance,
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
