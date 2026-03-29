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
CANASTA_BIN = os.path.join(REPO_ROOT, "canasta")

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
    api_url = (
        "http://127.0.0.1:%s/w/api.php"
        "?action=query&meta=siteinfo&format=json" % http_port
    )
    deadline = time.time() + timeout
    last_err = ""
    attempt = 0
    print("  Waiting for wiki at port %s..." % http_port, flush=True)
    while time.time() < deadline:
        attempt += 1
        elapsed = int(time.time() + timeout - deadline)
        try:
            req = urllib.request.Request(api_url)
            req.add_header("Host", "localhost")
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
    output = inst.run_ok("list")
    assert inst.id in output, "Instance not in list output"


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
    wait_for_wiki(inst.http_port)

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
    output = inst.run_ok("backup", "list", "-i", inst.id)
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
    # Extract snapshot ID from list output
    snapshot_id = None
    for line in output.split("\n"):
        if "test-snapshot" in line:
            snapshot_id = line.split()[0]
            break
    assert snapshot_id, "Could not extract snapshot ID"
    inst.run_ok(
        "backup", "restore", "-i", inst.id,
        "-s", snapshot_id, "--skip-safety-backup",
    )

    print("Verifying wiki accessible after restore...")
    wait_for_wiki(inst.http_port, timeout=180)
    shutil.rmtree(backup_dir, ignore_errors=True)


def test_gitops(inst):
    """Init gitops, verify templates, push, verify remote."""
    # Check prerequisites
    if shutil.which("git-crypt") is None:
        print("SKIP: git-crypt not installed")
        return

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

    # Verify initial commit in bare repo
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
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
        ["git", "log", "--oneline", "-1"],
        cwd=bare_repo, capture_output=True, text=True,
    )
    assert "Configuration update" in result.stdout, (
        "Push commit not found: %s" % result.stdout
    )

    # Verify file content in bare repo
    result = subprocess.run(
        ["git", "show", "HEAD:config/settings/global/GitopsTest.php"],
        cwd=bare_repo, capture_output=True, text=True,
    )
    assert "wgTestSetting" in result.stdout, (
        "File content not in repo: %s" % result.stdout
    )


# --- Test runner ---

ALL_TESTS = {
    "lifecycle": test_lifecycle,
    "import": test_import_export,
    "upgrade": test_upgrade,
    "backup": test_backup,
    "gitops": test_gitops,
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
