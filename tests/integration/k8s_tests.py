"""Integration tests for Kubernetes instances, run by run_tests.py.

They need a k3s cluster that `canasta install k8s-cp` set up on the host,
and the integration-k8s CI job runs them.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from run_tests import REPO_ROOT, SkipTest, TestInstance, read_env, require_docker


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

    # 'gitops sync' triggers an Argo CD Application sync (Compose is a no-op).
    # This lifecycle test creates with --skip-argocd-install, so an
    # Application CR only exists when Argo CD is already running on the runner.
    # Gate on that: with an Application present, sync must patch it with the
    # hard-refresh annotation and report completion; without Argo CD there is
    # nothing to reconcile, so the command must surface a clear error rather
    # than silently succeeding.
    app_exists = subprocess.run(
        ["kubectl", "get", "application", "canasta-%s" % inst.id,
         "-n", "argocd"],
        capture_output=True,
    ).returncode == 0
    if app_exists:
        print("Triggering an Argo CD sync via gitops sync...")
        output = inst.run_ok("gitops", "sync", "-i", inst.id)
        assert "sync completed" in output.lower(), (
            "gitops sync should report Argo CD sync completion:\n%s" % output
        )
        refresh = subprocess.run(
            ["kubectl", "get", "application", "canasta-%s" % inst.id,
             "-n", "argocd", "-o",
             "jsonpath={.metadata.annotations.argocd\\.argoproj\\.io/refresh}"],
            capture_output=True, text=True,
        ).stdout.strip()
        # The controller clears the annotation once it processes the refresh,
        # so accept either the annotation still set to 'hard' or already
        # consumed (empty) — both mean the patch reached the Application.
        assert refresh in ("hard", ""), (
            "gitops sync did not apply the hard-refresh annotation: %r" % refresh
        )
    else:
        print("No Argo CD Application present; asserting gitops sync errors...")
        out, rc = inst.run("gitops", "sync", "-i", inst.id)
        assert rc != 0, (
            "gitops sync should fail with no Argo CD Application to sync:\n%s"
            % out
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

    # Verify that `canasta config set` on a K8s instance actually
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

    # Verify that config set propagates values.yaml-backed knobs
    # on K8s. Previously, setting CANASTA_ENABLE_VARNISH via config set
    # updated .env but not values.yaml, so the Helm chart and the
    # running pod never saw the change.
    print("Testing config set → values.yaml propagation (#53)...")
    import yaml as _yaml  # for reading values.yaml

    values_path = os.path.join(inst.instance_path(), "values.yaml")

    # First, hand-edit replicaCount into values.yaml to verify it
    # survives the config set round-trip: unrelated hand-edits must not be clobbered.
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


def test_k8s_backup(inst):
    """K8s backup end-to-end on a single-node cluster.

    Exercises the runtime backup path that previously had only unit
    coverage: a local (hostPath) restic repo, the on-demand backup Job
    with its dump-databases init container, and the host-crontab schedule
    lifecycle, and a full restore: the restore pod, the pre-restore safety
    snapshot, the database import, and restic --delete pruning the images
    volume back to the snapshot.
    """
    result = subprocess.run(["kubectl", "cluster-info"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("kubectl not available or cluster not reachable")
    result = subprocess.run(["helm", "version", "--short"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("helm not installed")

    ns = "canasta-%s" % inst.id
    cronjob = "canasta-backup-%s" % inst.id
    # Local restic repo as a hostPath on the node. k8s_run_backup mounts
    # it with type DirectoryOrCreate, so the path is created on demand.
    repo = "/var/lib/canasta-k8s-backup-test-%s" % inst.id

    # Backup repo config has to be present before create so it lands in
    # the backup-env Secret the Job reads.
    with open(inst.env_file, "a") as f:
        f.write("RESTIC_REPOSITORY=%s\n" % repo)
        f.write("RESTIC_PASSWORD=testpass\n")

    try:
        print("Creating Kubernetes instance...")
        inst.run_ok(
            "create", "-i", inst.id, "-w", "main",
            "-n", "localhost", "-p", inst.work_dir,
            "--orchestrator", "kubernetes",
            "--skip-argocd-install",
            "-e", inst.env_file,
        )

        # The dump-databases init container connects to the db, so the
        # db StatefulSet must be ready before a backup can succeed.
        print("Waiting for the database to be ready...")
        result = subprocess.run(
            ["kubectl", "rollout", "status",
             "statefulset/canasta-%s-db" % inst.id,
             "-n", ns, "--timeout=300s"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            "db not ready: %s\n%s" % (result.stdout, result.stderr)
        )

        def web_pod():
            result = subprocess.run(
                ["kubectl", "rollout", "status",
                 "deployment/canasta-%s-web" % inst.id,
                 "-n", ns, "--timeout=300s"],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, (
                "web not ready: %s\n%s" % (result.stdout, result.stderr)
            )
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", ns,
                 "-l", "app.kubernetes.io/component=web",
                 "--field-selector=status.phase=Running",
                 "-o", "jsonpath={.items[0].metadata.name}"],
                capture_output=True, text=True,
            )
            assert result.returncode == 0 and result.stdout.strip(), (
                "Could not find web pod: %s" % result.stderr
            )
            return result.stdout.strip()

        def in_web(script, stdin=None):
            # A pod from the previous rollout can still be terminating
            # just after a restart, so a failed exec is retried.
            for _ in range(6):
                result = subprocess.run(
                    ["kubectl", "exec", "-i", "-n", ns, web_pod(), "-c", "web",
                     "--", "sh", "-c", script],
                    input=stdin, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    break
                time.sleep(10)
            assert result.returncode == 0, (
                "web pod command failed: %s\n%s\n%s"
                % (script, result.stdout, result.stderr)
            )
            return result.stdout

        page = "Restore_sentinel"

        def edit_page(text):
            in_web("cd /var/www/mediawiki/w && php maintenance/run.php edit "
                   "--wiki=main --summary=test %s" % page, stdin=text)

        def page_text():
            return in_web("cd /var/www/mediawiki/w && php maintenance/run.php "
                          "getText --wiki=main %s" % page).strip()

        print("Creating a sentinel page...")
        edit_page("before the snapshot")
        assert page_text() == "before the snapshot"

        print("Initializing backup repository...")
        inst.run_ok("backup", "init", "-i", inst.id)

        print("Creating backup snapshot...")
        inst.run_ok("backup", "create", "-i", inst.id, "-t", "k8s-test-snapshot")

        print("Listing snapshots...")
        output = inst.run_quiet("backup", "list", "-i", inst.id)
        assert "k8s-test-snapshot" in output, (
            "Snapshot tag not found in list output:\n%s" % output
        )
        snapshot_id = None
        for line in output.splitlines():
            if "k8s-test-snapshot" in line:
                match = re.search(r'\b([0-9a-f]{8,})\b', line)
                if match:
                    snapshot_id = match.group(1)
                    break
        assert snapshot_id, "Could not extract snapshot ID from:\n%s" % output

        print("Checking the snapshot holds the database dump...")
        files = inst.run_quiet(
            "backup", "files", "-i", inst.id, "-s", snapshot_id)
        assert "db_main.sql" in files, (
            "the wiki's dump is missing from the snapshot:\n%s" % files
        )

        print("Changing the page and adding an upload after the snapshot...")
        edit_page("after the snapshot")
        assert page_text() == "after the snapshot"
        stray = "/mediawiki/images/restore-sentinel-after-snapshot.txt"
        in_web("echo stray > %s" % stray)

        print("Restoring the snapshot...")
        inst.run_ok("backup", "restore", "-i", inst.id, "-s", snapshot_id)

        print("Verifying the page came back...")
        assert page_text() == "before the snapshot", (
            "the restore did not bring back the page as it was in the "
            "snapshot"
        )
        print("Verifying the post-snapshot upload is gone...")
        assert in_web("test -e %s && echo present || echo absent"
                      % stray).strip() == "absent", (
            "a file added after the snapshot survived a full restore"
        )
        marker = os.path.join(inst.instance_path(),
                              ".canasta-restore-in-progress")
        assert not os.path.exists(marker), (
            "the restore marker was left behind after a successful restore"
        )

        print("Checking the restore took a safety snapshot...")
        output = inst.run_quiet("backup", "list", "-i", inst.id)
        safety_id = None
        for line in output.splitlines():
            if "safety-before-restore" in line:
                match = re.search(r'\b([0-9a-f]{8,})\b', line)
                if match:
                    safety_id = match.group(1)
        assert safety_id, (
            "no safety-before-restore snapshot in list output:\n%s" % output
        )

        print("Diffing the snapshot against the safety snapshot...")
        inst.run_ok(
            "backup", "diff", "-i", inst.id,
            "-s", snapshot_id, "--snapshot2", safety_id,
        )

        print("Setting a backup schedule (host crontab on the cp node)...")
        inst.run_ok("backup", "schedule", "set", "-i", inst.id, "0 3 * * *")
        # Scheduling is a host crontab entry now, not an in-cluster CronJob.
        # The CI runner is the single-node control plane, so the entry lands
        # in its crontab.
        marker = "canasta-backup-%s" % inst.id
        crontab = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True,
        )
        assert marker in crontab.stdout, (
            "crontab entry %s not written:\n%s" % (marker, crontab.stdout)
        )
        no_cronjob = subprocess.run(
            ["kubectl", "get", "cronjob", cronjob, "-n", ns],
            capture_output=True,
        )
        assert no_cronjob.returncode != 0, (
            "an in-cluster CronJob was created; scheduling should use the "
            "host crontab"
        )

        print("Listing the backup schedule...")
        output = inst.run_quiet("backup", "schedule", "list", "-i", inst.id)
        assert "0 3 * * *" in output, (
            "Schedule not shown in list output:\n%s" % output
        )

        print("Removing the backup schedule...")
        inst.run_ok("backup", "schedule", "remove", "-i", inst.id)
        crontab = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True,
        )
        assert marker not in crontab.stdout, (
            "crontab entry still present after remove:\n%s" % crontab.stdout
        )
    finally:
        # The hostPath repo lives on the node (created by the Job as root);
        # cleanup() removes the instance but not this path.
        subprocess.run(["sudo", "rm", "-rf", repo], capture_output=True)


# Argo CD's repo-server runs in a pod, so a K8s gitops remote has to be a
# network address, not a local bare repo. The CLI speaks SSH with a deploy key
# to it, and trusts a self-hosted host by keyscanning it into Argo CD's
# known-hosts, so serve the bare repo over sshd on the host network: the
# runner pushes to it and pods reach it at the same host IP.
_K8S_GIT_SERVER_IMAGE = "alpine:3.20"


_K8S_GIT_SERVER_PORT = "2222"


_K8S_GIT_REPO_PATH = "/srv/git/config.git"


def _host_ip():
    """An IPv4 address of this host that pods in the cluster can reach."""
    out = subprocess.run(
        ["ip", "-4", "route", "get", "1.1.1.1"],
        capture_output=True, text=True,
    ).stdout
    match = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", out)
    if match:
        return match.group(1)
    addrs = subprocess.run(
        ["hostname", "-I"], capture_output=True, text=True,
    ).stdout.split()
    return addrs[0] if addrs else ""


def _start_ssh_git_server(name, port, pubkey):
    """Run sshd with an empty bare repo that accepts `pubkey` as user git."""
    script = (
        "set -e; "
        "apk add --no-cache openssh-server git >/dev/null; "
        "adduser -D -s /bin/sh git; "
        # busybox adduser -D locks the account, and sshd built without PAM
        # refuses every login to a locked account, keys included.
        "sed -i 's/^git:[^:]*:/git:*:/' /etc/shadow; "
        "mkdir -p /home/git/.ssh; "
        'printf "%%s\\n" "$GIT_PUBKEY" > /home/git/.ssh/authorized_keys; '
        "chmod 700 /home/git/.ssh; chmod 600 /home/git/.ssh/authorized_keys; "
        "chown -R git:git /home/git/.ssh; "
        "mkdir -p %(repo)s; "
        "git init -q --bare --initial-branch=main %(repo)s; "
        "chown -R git:git /srv/git; "
        "ssh-keygen -A; "
        "exec /usr/sbin/sshd -D -e -p %(port)s"
    ) % {"repo": _K8S_GIT_REPO_PATH, "port": port}
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", name, "--network", "host",
         "-e", "GIT_PUBKEY=%s" % pubkey,
         _K8S_GIT_SERVER_IMAGE, "sh", "-c", script],
        capture_output=True, text=True, check=True,
    )


def _ssh_git_server_repo(name, *git_args):
    """Run git against the served bare repo, as its owner."""
    return subprocess.run(
        ["docker", "exec", "-u", "git", name,
         "git", "-C", _K8S_GIT_REPO_PATH] + list(git_args),
        capture_output=True, text=True,
    )


def _wait_ssh_git_server(name, host, port, repo_url, key, timeout=180):
    """Wait until sshd answers and the deploy key can read the repo."""
    ssh_cmd = (
        "ssh -i %s -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        "-o BatchMode=yes" % shlex.quote(key)
    )
    env = dict(os.environ, GIT_SSH_COMMAND=ssh_cmd)
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        scan = subprocess.run(
            ["ssh-keyscan", "-T", "5", "-p", port, host],
            capture_output=True, text=True,
        )
        if scan.returncode == 0 and scan.stdout.strip():
            probe = subprocess.run(
                ["git", "ls-remote", repo_url],
                capture_output=True, text=True, env=env,
            )
            if probe.returncode == 0:
                return
            last = probe.stderr
        else:
            last = scan.stderr
        time.sleep(5)
    logs = subprocess.run(
        ["docker", "logs", "--tail", "50", name],
        capture_output=True, text=True,
    )
    raise AssertionError(
        "SSH git server at %s:%s not usable after %ds: %s\n"
        "container logs:\n%s%s"
        % (host, port, timeout, last.strip(), logs.stdout, logs.stderr)
    )


def _argo_app_state(app, argocd_ns="argocd"):
    """(sync status, health status, synced revision) of an Application."""
    result = subprocess.run(
        ["kubectl", "get", "application", app, "-n", argocd_ns, "-o",
         'jsonpath={.status.sync.status}{"|"}{.status.health.status}'
         '{"|"}{.status.sync.revision}'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ("missing", result.stderr.strip(), "")
    fields = (result.stdout.split("|") + ["", "", ""])[:3]
    return tuple(fields)


def _wait_argo_app_synced(app, revision, timeout=600):
    """Wait until the Application is Synced and Healthy at `revision`."""
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        state = _argo_app_state(app)
        if (state[0] == "Synced" and state[1] == "Healthy"
                and state[2] == revision):
            return
        time.sleep(10)
    raise AssertionError(
        "Argo CD Application %s not Synced/Healthy at %s after %ds; "
        "last (sync, health, revision): %r" % (app, revision, timeout, state)
    )


def _k8s_gitops_diagnostics(namespace, git_server):
    """Print what explains an Argo CD sync or gitops failure."""
    print("--- K8s gitops failure diagnostics ---", flush=True)
    for cmd in (
        ["kubectl", "get", "application", "-n", "argocd", "-o", "yaml"],
        ["kubectl", "logs", "-n", "argocd", "deployment/argocd-repo-server",
         "--all-containers", "--tail=100"],
        ["kubectl", "logs", "-n", "argocd",
         "statefulset/argocd-application-controller", "--tail=100"],
        ["kubectl", "get", "configmap", "argocd-ssh-known-hosts-cm",
         "-n", "argocd", "-o", "yaml"],
        ["kubectl", "get", "all,ingress,pvc", "-n", namespace],
        ["docker", "logs", "--tail", "50", git_server],
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
        print("$ %s\n%s%s" % (" ".join(cmd), result.stdout, result.stderr),
              flush=True)


def _assert_gitops_status_clean(output, when):
    """`gitops status` shows nothing to push, pull or capture."""
    assert "Ahead of remote:  0" in output and "Behind remote:    0" in output, (
        "gitops status should be in step with the remote %s:\n%s"
        % (when, output)
    )
    for advisory in ("Staged for push", "Unstaged changes",
                     "Untracked files", "Uncaptured config/wikis.yaml"):
        assert advisory not in output, (
            "gitops status should be clean %s, but shows %r:\n%s"
            % (when, advisory, output)
        )


def test_k8s_gitops(inst):
    """K8s gitops through Argo CD: init, add a wiki and push, upgrade,
    init --reinit, then delete.

    Creates without --skip-argocd-install, so create installs Argo CD and
    the Application it is handed has to clone the remote from inside the
    cluster.
    """
    result = subprocess.run(["kubectl", "cluster-info"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("kubectl not available or cluster not reachable")
    result = subprocess.run(["helm", "version", "--short"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("helm not installed")
    require_docker()

    host_ip = _host_ip()
    assert host_ip, "could not determine this host's IPv4 address"

    ns = "canasta-%s" % inst.id
    app = "canasta-%s" % inst.id
    git_server = "canasta-int-gitserver-%s" % inst.id
    repo_url = "ssh://git@%s:%s%s" % (
        host_ip, _K8S_GIT_SERVER_PORT, _K8S_GIT_REPO_PATH)
    host_name = "testhost"
    wiki_host = "docs.localhost"

    # init reuses a deploy key already at <key>.ssh, so generating it here
    # lets the git server trust it before init probes the remote.
    key_base = os.path.join(inst.work_dir, "gitops-k8s.key")
    deploy_key = key_base + ".ssh"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
         "canasta-%s" % inst.id, "-f", deploy_key],
        capture_output=True, check=True,
    )
    with open(deploy_key + ".pub") as f:
        pubkey = f.read().strip()

    print("Starting the SSH git server at %s..." % repo_url)
    _start_ssh_git_server(git_server, _K8S_GIT_SERVER_PORT, pubkey)
    try:
        _wait_ssh_git_server(
            git_server, host_ip, _K8S_GIT_SERVER_PORT, repo_url, deploy_key)

        print("Creating Kubernetes instance with Argo CD...")
        inst.run_ok(
            "create", "-i", inst.id, "-w", "main",
            "-n", "localhost", "-p", inst.work_dir,
            "--orchestrator", "kubernetes",
        )
        argocd = subprocess.run(
            ["kubectl", "get", "deployment", "argocd-server", "-n", "argocd"],
            capture_output=True, text=True,
        )
        assert argocd.returncode == 0, (
            "create should install Argo CD: %s" % argocd.stderr
        )

        print("Initializing gitops...")
        inst.run_ok(
            "gitops", "init", "-i", inst.id, "-n", host_name,
            "--repo", repo_url, "--key", key_base, "--non-interactive",
        )
        log = _ssh_git_server_repo(git_server, "log", "--format=%s", "main")
        assert "Initial gitops configuration" in log.stdout, (
            "init's commit is not on the remote: %s%s"
            % (log.stdout, log.stderr)
        )
        head = _ssh_git_server_repo(git_server, "rev-parse", "main").stdout.strip()

        print("Waiting for the Argo CD Application to sync %s..." % head[:12])
        _wait_argo_app_synced(app, head)

        output = inst.run_quiet("gitops", "status", "-i", inst.id)
        _assert_gitops_status_clean(output, "after init")

        print("Adding wiki %s..." % wiki_host)
        inst.run_ok("add", "-i", inst.id, "-w", "docs", "-u", wiki_host)

        message = "Add the docs wiki"
        print("Pushing...")
        inst.run_ok("gitops", "push", "-i", inst.id, "-m", message)
        log = _ssh_git_server_repo(git_server, "log", "-1", "--format=%s", "main")
        assert log.stdout.strip() == message, (
            "push should commit with the given message; remote head is %r"
            % log.stdout.strip()
        )
        import yaml
        rendered = _ssh_git_server_repo(
            git_server, "show",
            "main:hosts/%s/rendered-values.yaml" % host_name)
        assert rendered.returncode == 0, (
            "rendered values missing from the remote: %s" % rendered.stderr
        )
        domains = (yaml.safe_load(rendered.stdout) or {}).get("domains") or []
        assert wiki_host in domains, (
            "pushed rendered values should route %s; domains: %r"
            % (wiki_host, domains)
        )
        head = _ssh_git_server_repo(git_server, "rev-parse", "main").stdout.strip()

        print("Syncing Argo CD to %s..." % head[:12])
        inst.run_ok("gitops", "sync", "-i", inst.id)
        _wait_argo_app_synced(app, head)

        hosts = subprocess.run(
            ["kubectl", "get", "ingress", "-n", ns, "-o",
             "jsonpath={.items[*].spec.rules[*].host}"],
            capture_output=True, text=True,
        )
        assert wiki_host in hosts.stdout.split(), (
            "the Ingress should route %s after the sync; hosts: %r %s"
            % (wiki_host, hosts.stdout, hosts.stderr)
        )

        output = inst.run_quiet("gitops", "status", "-i", inst.id)
        _assert_gitops_status_clean(output, "after push")

        print("Upgrading...")
        inst.run_ok("upgrade")
        output = inst.run_quiet("gitops", "status", "-i", inst.id)
        _assert_gitops_status_clean(output, "after upgrade")

        print("Re-initializing gitops with --reinit...")
        inst.run_ok(
            "gitops", "init", "-i", inst.id, "-n", host_name,
            "--repo", repo_url, "--key", key_base, "--non-interactive",
            "--reinit", "--force",
        )
        log = _ssh_git_server_repo(git_server, "log", "-1", "--format=%s", "main")
        assert log.stdout.strip() == "Initial gitops configuration", (
            "--reinit should push a fresh initial commit; remote head is %r"
            % log.stdout.strip()
        )
        head = _ssh_git_server_repo(git_server, "rev-parse", "main").stdout.strip()
        print("Syncing Argo CD to the re-initialized %s..." % head[:12])
        inst.run_ok("gitops", "sync", "-i", inst.id)
        _wait_argo_app_synced(app, head)

        repo_secret = "canasta-%s-repo" % inst.id
        assert subprocess.run(
            ["kubectl", "get", "secret", repo_secret, "-n", "argocd"],
            capture_output=True,
        ).returncode == 0, "gitops init should create Secret %s" % repo_secret

        print("Deleting instance...")
        inst.run_ok("delete", "-i", inst.id, "--yes")
        secret = subprocess.run(
            ["kubectl", "get", "secret", repo_secret, "-n", "argocd"],
            capture_output=True, text=True,
        )
        assert secret.returncode != 0, (
            "the Argo CD repository Secret %s survived delete" % repo_secret
        )
        for _ in range(24):
            gone = subprocess.run(
                ["kubectl", "get", "application", app, "-n", "argocd"],
                capture_output=True,
            ).returncode != 0
            if gone:
                break
            time.sleep(5)
        assert gone, "the Argo CD Application survived delete"
        for _ in range(36):
            ns_gone = subprocess.run(
                ["kubectl", "get", "namespace", ns], capture_output=True,
            ).returncode != 0
            if ns_gone:
                break
            time.sleep(5)
        assert ns_gone, "namespace %s survived delete" % ns
        argocd = subprocess.run(
            ["kubectl", "get", "deployment", "argocd-server", "-n", "argocd"],
            capture_output=True, text=True,
        )
        assert argocd.returncode == 0, (
            "delete must leave the shared Argo CD install: %s" % argocd.stderr
        )
    except Exception:
        _k8s_gitops_diagnostics(ns, git_server)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", git_server], capture_output=True)


def _k8s_pod_containers_ready(ns, component):
    """`name=ready` pairs for the containers of the first Running pod of
    ``component``."""
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", ns,
         "-l", "app.kubernetes.io/component=%s" % component,
         "--field-selector=status.phase=Running",
         "-o", "jsonpath={range .items[0].status.containerStatuses[*]}"
         "{.name}={.ready} {end}"],
        capture_output=True, text=True,
    )
    return result.stdout.split()


def _k8s_caddy_ready(inst, timeout=300):
    """Wait until the caddy pod is Running with BOTH containers ready
    (caddy + the crowdsec-agent sidecar) and the CrowdSec local API pod is
    ready. If caddy were left on the stock image it would crashloop on the
    crowdsec Caddyfile directive and never reach ready, so this doubles as
    that regression guard."""
    ns = "canasta-%s" % inst.id
    deadline = time.time() + timeout
    last = ""
    print("  Waiting for caddy, its crowdsec agent and the local API to be "
          "ready...", flush=True)
    while time.time() < deadline:
        caddy = _k8s_pod_containers_ready(ns, "caddy")
        lapi = _k8s_pod_containers_ready(ns, "crowdsec")
        last = "caddy pod: %s; crowdsec pod: %s" % (caddy, lapi)
        if ("caddy=true" in caddy and "crowdsec-agent=true" in caddy
                and "crowdsec=true" in lapi):
            print("  caddy, the crowdsec agent and the local API are ready")
            return
        time.sleep(5)
    raise AssertionError(
        "caddy + crowdsec not ready within %ds (last: %s)" % (timeout, last)
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
    surface. This is the coverage gap that let two blockers reach main —
    an unquoted preflight jsonpath that broke every K8s crowdsec command, and
    caddy stuck on the stock image (crashloop) — because the K8s
    integration job never *enabled* CrowdSec. Unit tests assert file content,
    not runtime; only an enable-on-a-real-cluster test catches this.

    CrowdSec is enabled at CREATE time (-e). That path otherwise has no CI
    coverage (it was once broken there), and it does one fewer full-stack
    restart than the config-set-enable path — config set enable bounces the
    whole stack twice (enable + the bouncer-key store), and on a 2-CPU CI
    runner the MediaWiki pod's healthcheck start-period makes a second full
    restart blow helm's --wait. The restart-light command surface below
    (reload only rolls the CrowdSec local API, not the stack) keeps this
    within the runner's budget.

    The live 'banned IP gets a 403' assertion is intentionally omitted: in the
    CI cluster the client IP Caddy sees is environment-dependent (same reasoning
    as the Compose test_crowdsec). This pins the deterministic behavior: the
    agent and local API come up and caddy runs the plugin image, every
    command exits clean, and ban/unban decisions plumb through the LAPI."""
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

    # The caddy deployment must run the plugin image and the pod must
    # reach 2/2 (caddy + crowdsec agent). Stock image -> crashloop, never 2/2.
    _k8s_caddy_ready(inst)
    image = _k8s_caddy_image(inst)
    import yaml as _yaml
    with open(os.path.join(REPO_ROOT, "vars", "images.yml")) as f:
        plugin_image = _yaml.safe_load(f)["canasta_caddy_plugin_image"]
    assert image == plugin_image, (
        "caddy must run the plugin image when CrowdSec is on, got: %r" % image
    )

    # The deploy-time reconcile must record both knobs in values.yaml.
    with open(os.path.join(inst.instance_path(), "values.yaml")) as f:
        values = _yaml.safe_load(f)
    assert values.get("crowdsec", {}).get("enabled") is True, (
        "values.yaml crowdsec.enabled must be true, got: %s"
        % values.get("crowdsec")
    )

    # Every K8s crowdsec command must exec cleanly into the local API or
    # the agent.
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

    # reload / reload --purge-blocklists roll ONLY the local API deployment,
    # not the whole stack — light enough for CI. The Recreate rollout renames
    # the local API pod, so this also exercises the post-restart cscli prefix
    # re-resolve.
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


class _IngressProbe:
    """Polls the wiki through the ingress in a background thread and records
    every answer, so a test can assert that nothing failed while it acted."""

    URL = "http://127.0.0.1/w/api.php?action=query&meta=siteinfo&format=json"

    def __init__(self):
        import threading
        self.results = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @classmethod
    def once(cls):
        """The HTTP status for one request, or the error text."""

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        req = urllib.request.Request(cls.URL)
        req.add_header("Host", "localhost")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:
            return "%s: %s" % (type(e).__name__, e)

    def _run(self):
        while not self._stop.is_set():
            self.results.append((time.time(), self.once()))
            self._stop.wait(0.25)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()

    def failures(self):
        return [(t, r) for t, r in self.results
                if not isinstance(r, int) or r >= 500]


def _k8s_pod_names(ns, component):
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", ns,
         "-l", "app.kubernetes.io/component=%s" % component,
         "-o", "jsonpath={.items[*].metadata.name}"],
        capture_output=True, text=True,
    )
    return set(result.stdout.split())


def _wait_for_output(inst, needle, timeout, *args):
    """Poll a canasta command until its output contains ``needle``."""
    deadline = time.time() + timeout
    out = ""
    while time.time() < deadline:
        out = inst.run_quiet(*args)
        if needle in out:
            return out
        time.sleep(10)
    raise AssertionError(
        "'canasta %s' never showed %r within %ds:\n%s"
        % (" ".join(args), needle, timeout, out))


def test_k8s_crowdsec_rolling(inst):
    """Kubernetes: with CrowdSec on, a Caddy change rolls without an outage.

    The CrowdSec local API runs in its own Deployment holding the PVC, and
    an agent sidecar in the caddy pod parses the access log, so the caddy
    Deployment rolls (the new pod is Ready before the old one stops) instead
    of recreating. This drives a crowdsec config edit through 'crowdsec
    reload', which rolls the caddy pod and restarts the local API, while a
    probe polls the wiki through the ingress, and asserts no request failed.
    It also checks that the agent reports to the local API and reads the
    access log, and that a ban survives a local API restart without the
    caddy pod restarting."""
    result = subprocess.run(["kubectl", "cluster-info"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("kubectl not available or cluster not reachable")
    result = subprocess.run(["helm", "version", "--short"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("helm not installed")
    ns = "canasta-%s" % inst.id

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
    try:
        _k8s_caddy_ready(inst)
        env = read_env(inst.env_path())
        assert env.get("CROWDSEC_AGENT_PASSWORD"), (
            "create should generate CROWDSEC_AGENT_PASSWORD")
        _wait_bouncer_active(inst)

        print("Waiting for the wiki to answer through the ingress...")
        deadline = time.time() + 300
        answer = None
        while time.time() < deadline:
            answer = _IngressProbe.once()
            if isinstance(answer, int) and answer < 500:
                break
            time.sleep(5)
        assert isinstance(answer, int) and answer < 500, (
            "the wiki never answered through the ingress (last: %s)" % answer)

        print("Checking the agent reports and reads the access log...")
        _wait_for_output(inst, "Agent: reporting", 300,
                         "crowdsec", "status", "-i", inst.id)
        _wait_for_output(inst, "/var/log/caddy/access.log", 300,
                         "crowdsec", "metrics", "-i", inst.id)

        # 203.0.113.0/24 is TEST-NET-3 (documentation range) — safe to ban.
        inst.run_ok("crowdsec", "ban", "203.0.113.9", "-i", inst.id)

        print("Editing the whitelist and reloading under load...")
        whitelist = os.path.join(inst.instance_path(), "config", "crowdsec",
                                 "whitelists.yaml")
        with open(whitelist, "a") as f:
            f.write("# rolled by the integration test\n")
        caddy_before = _k8s_pod_names(ns, "caddy")
        lapi_before = _k8s_pod_names(ns, "crowdsec")
        with _IngressProbe() as probe:
            inst.run_ok("crowdsec", "reload", "-i", inst.id)
            subprocess.run(
                ["kubectl", "rollout", "status",
                 "deployment/canasta-%s-caddy" % inst.id, "-n", ns,
                 "--timeout=300s"], check=True)
            # Requests keep arriving while the old pod drains.
            time.sleep(15)
        _k8s_caddy_ready(inst)
        caddy_after = _k8s_pod_names(ns, "caddy")
        assert not caddy_before & caddy_after, (
            "the whitelist edit should roll the caddy pod (%s -> %s)"
            % (caddy_before, caddy_after))
        assert not lapi_before & _k8s_pod_names(ns, "crowdsec"), (
            "reload should restart the local API")
        failures = probe.failures()
        print("  %d requests during the roll, %d failed"
              % (len(probe.results), len(failures)))
        assert probe.results, "the probe made no requests"
        assert not failures, (
            "requests failed while the caddy pod rolled: %s" % failures[:20])

        print("Restarting the local API alone...")
        caddy_before = _k8s_pod_names(ns, "caddy")
        with _IngressProbe() as probe:
            inst.run_ok("crowdsec", "reload", "-i", inst.id)
        _k8s_caddy_ready(inst)
        assert _k8s_pod_names(ns, "caddy") == caddy_before, (
            "a reload with no config change must leave the caddy pod alone")
        assert not probe.failures(), (
            "requests failed while the local API restarted: %s"
            % probe.failures()[:20])
        out = inst.run_quiet("crowdsec", "status", "-i", inst.id)
        assert "203.0.113.9" in out, (
            "the ban should survive a local API restart:\n%s" % out)
        _wait_for_output(inst, "Agent: reporting", 300,
                         "crowdsec", "status", "-i", inst.id)
    finally:
        print("Deleting instance...")
        inst.run("delete", "-i", inst.id, "--yes")


def test_k8s_build_from(inst):
    """`create -o kubernetes --build-from`: the custom image is built, pushed to
    the in-cluster registry, and the web pod runs it from the registry ref.

    Guards this end to end: without it the pod silently ran the stock
    ghcr image (wrong image ref) or never pulled (no registry). The build is a
    single FROM-stock layer plus a marker file, so the test exercises the
    build -> push -> pull delivery path without a heavyweight image build.
    """
    result = subprocess.run(["kubectl", "cluster-info"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("kubectl not available or cluster not reachable")
    result = subprocess.run(["helm", "version", "--short"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("helm not installed")

    # Trivial build-from source: a Canasta image identical to stock plus a marker
    # layer. canasta create --build-from runs `docker build` on <src>/Canasta.
    src_root = os.path.join(inst.work_dir, "bfsrc")
    src = os.path.join(src_root, "Canasta")
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, "Dockerfile"), "w") as f:
        f.write(
            "FROM ghcr.io/canastawiki/canasta:latest\n"
            "RUN echo build-from-ci > /tmp/canasta-build-from-ci\n"
        )

    print("Creating Kubernetes instance from source (--build-from)...")
    inst.run_ok(
        "create", "-i", inst.id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "--orchestrator", "kubernetes",
        "--build-from", src_root,
        "--skip-argocd-install",
    )

    print("Verifying instance registered...")
    output = inst.run_ok("list")
    assert inst.id in output, "instance not in list: %s" % output
    assert "KUBERNETES" in output, "instance not K8s: %s" % output

    namespace = "canasta-%s" % inst.id

    print("Verifying the web deployment runs the in-cluster registry image...")
    image = subprocess.run(
        ["kubectl", "-n", namespace, "get",
         "deployment/canasta-%s-web" % inst.id,
         "-o", "jsonpath={.spec.template.spec.containers[0].image}"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert image == "10.43.0.2:5000/canasta:local", (
        "web image is %r, expected the in-cluster registry ref "
        "(build_from image-ref fix)" % image
    )

    print("Verifying the web pod pulled the image and is running...")
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace,
         "-l", "app.kubernetes.io/component=web",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0 and result.stdout, (
        "could not find web pod (pull/registry failure?): %s" % result.stderr
    )
    web_pod = result.stdout.strip()
    phase = subprocess.run(
        ["kubectl", "get", "pod", web_pod, "-n", namespace,
         "-o", "jsonpath={.status.phase}"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert phase == "Running", "web pod not Running (phase=%s)" % phase

    print("Confirming the running pod is the built image (marker layer)...")
    marker = subprocess.run(
        ["kubectl", "exec", "-n", namespace, web_pod, "--",
         "cat", "/tmp/canasta-build-from-ci"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert marker == "build-from-ci", (
        "marker missing; pod is not the built image: %r" % marker
    )


def _k8s_require_cluster():
    """Skip unless kubectl reaches a cluster and helm is installed."""
    result = subprocess.run(["kubectl", "cluster-info"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("kubectl not available or cluster not reachable")
    result = subprocess.run(["helm", "version", "--short"], capture_output=True)
    if result.returncode != 0:
        raise SkipTest("helm not installed")


def _k8s_kubectl(*args):
    """Run kubectl and return its stripped stdout; fail on a non-zero exit."""
    result = subprocess.run(
        ["kubectl"] + list(args), capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "kubectl %s failed: %s\n%s"
        % (" ".join(args), result.stdout, result.stderr)
    )
    return result.stdout.strip()


def _k8s_wait_web(inst_id, timeout=300):
    """Wait for the instance's web Deployment to finish rolling out."""
    result = subprocess.run(
        ["kubectl", "rollout", "status",
         "deployment/canasta-%s-web" % inst_id,
         "-n", "canasta-%s" % inst_id, "--timeout=%ds" % timeout],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "web of %s did not roll out: %s\n%s"
        % (inst_id, result.stdout, result.stderr)
    )


def _k8s_running_pods(inst_id):
    """Map name -> uid of the instance's Running pods."""
    out = _k8s_kubectl(
        "get", "pods", "-n", "canasta-%s" % inst_id,
        "--field-selector=status.phase=Running",
        "-o", "jsonpath={range .items[*]}{.metadata.name}={.metadata.uid}"
        "{\"\\n\"}{end}",
    )
    pods = {}
    for line in out.splitlines():
        name, _, uid = line.partition("=")
        if name:
            pods[name] = uid
    return pods


def _k8s_in_web(inst_id, script):
    """Run a shell script in a Running web pod of the instance.

    A pod from the previous rollout can still be terminating just after
    a deploy, so a failed exec is retried against a freshly chosen pod.
    """
    ns = "canasta-%s" % inst_id
    result = None
    for _ in range(6):
        _k8s_wait_web(inst_id)
        pod = subprocess.run(
            ["kubectl", "get", "pods", "-n", ns,
             "-l", "app.kubernetes.io/component=web",
             "--field-selector=status.phase=Running",
             "-o", "jsonpath={.items[0].metadata.name}"],
            capture_output=True, text=True,
        ).stdout.strip()
        if pod:
            result = subprocess.run(
                ["kubectl", "exec", "-n", ns, pod, "-c", "web",
                 "--", "sh", "-c", script],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return result.stdout
        time.sleep(10)
    raise AssertionError(
        "web pod command failed in %s: %s\n%s"
        % (ns, script, result and (result.stdout + result.stderr))
    )


def _k8s_mw_eval(inst_id, wiki, php):
    """Evaluate PHP in MediaWiki's context for one wiki; return stdout."""
    return _k8s_in_web(
        inst_id,
        "cd /var/www/mediawiki/w && printf '%%s\\n' %s | "
        "php maintenance/run.php eval --wiki=%s"
        % (shlex.quote(php), shlex.quote(wiki)),
    )


def _k8s_wiki_answers(inst_id, wiki="main"):
    """Assert the wiki boots and reads its database from a web pod."""
    out = _k8s_mw_eval(
        inst_id, wiki,
        'echo "WIKI_OK=" . MediaWiki\\MediaWikiServices::getInstance()'
        '->getDBLoadBalancer()->getConnection(DB_REPLICA)'
        '->selectField("page", "COUNT(*)", [], "probe");',
    )
    assert re.search(r"^WIKI_OK=\d+$", out, re.MULTILINE), (
        "wiki %s of %s did not answer:\n%s" % (wiki, inst_id, out)
    )


def _k8s_extension_loaded(inst_id, wiki, name):
    out = _k8s_mw_eval(
        inst_id, wiki,
        'echo "EXT_LOADED=" . (ExtensionRegistry::getInstance()'
        '->isLoaded("%s") ? 1 : 0);' % name,
    )
    for line in out.splitlines():
        if line.strip() in ("EXT_LOADED=1", "EXT_LOADED=0"):
            return line.strip() == "EXT_LOADED=1"
    raise AssertionError(
        "extension probe for %s gave no answer:\n%s" % (name, out)
    )


def _k8s_ingress_hosts(inst_id):
    out = _k8s_kubectl(
        "get", "ingress", "canasta-%s" % inst_id,
        "-n", "canasta-%s" % inst_id,
        "-o", "jsonpath={.spec.rules[*].host}",
    )
    return set(out.split())


def _k8s_web_image(inst_id):
    return _k8s_kubectl(
        "get", "deployment", "canasta-%s-web" % inst_id,
        "-n", "canasta-%s" % inst_id,
        "-o", "jsonpath={.spec.template.spec.containers"
        "[?(@.name=='web')].image}",
    )


def _k8s_running_web_images(inst_id):
    out = _k8s_kubectl(
        "get", "pods", "-n", "canasta-%s" % inst_id,
        "-l", "app.kubernetes.io/component=web",
        "--field-selector=status.phase=Running",
        "-o", "jsonpath={range .items[*]}{.spec.containers"
        "[?(@.name=='web')].image}{\"\\n\"}{end}",
    )
    return set(out.split())


def _k8s_wait_namespace_gone(ns):
    for _ in range(24):
        result = subprocess.run(
            ["kubectl", "get", "namespace", ns], capture_output=True,
        )
        if result.returncode != 0:
            return
        time.sleep(5)
    raise AssertionError("namespace %s still exists after delete" % ns)


def _k8s_values(inst):
    import yaml as _yaml
    with open(os.path.join(inst.instance_path(), "values.yaml")) as f:
        return _yaml.safe_load(f) or {}


def test_k8s_multi_instance(inst):
    """Two instances share one cluster without disturbing each other.

    Each instance has its own namespace, Helm release and Ingress host.
    Upgrading moves the instance on an older managed image pin to the
    current one and rolls its pods, while the instance on a custom image
    keeps it. Stopping and then deleting one instance must leave the
    other's pods running untouched and the cluster's shared workloads
    (ingress controller, in-cluster registry, cert-manager when present)
    in place.
    """
    _k8s_require_cluster()

    with open(os.path.join(REPO_ROOT, "CANASTA_VERSION")) as f:
        current = f.read().strip()
    # A published release older than the one this CLI targets: the
    # version-shaped tag a previous CLI would have pinned.
    pinned = "3.5.25"
    assert pinned != current, (
        "the older pin must differ from CANASTA_VERSION (%s)" % current
    )
    repo = "ghcr.io/canastawiki/canasta"

    inst_b = TestInstance("inttest-k8s-multi-b")
    # One controller managing both instances: B shares A's registry.
    shutil.rmtree(inst_b.config_dir, ignore_errors=True)
    inst_b.config_dir = inst.config_dir
    a, b = inst.id, inst_b.id
    ns_a, ns_b = "canasta-%s" % a, "canasta-%s" % b
    host_a, host_b = "multi-a.localhost", "multi-b.localhost"

    def other_workloads():
        out = _k8s_kubectl(
            "get", "deployments,daemonsets,statefulsets", "-A",
            "-o", "jsonpath={range .items[*]}{.kind}/{.metadata.namespace}/"
            "{.metadata.name}{\"\\n\"}{end}",
        )
        return {
            line for line in out.splitlines()
            if line and line.split("/")[1] not in (ns_a, ns_b)
        }

    try:
        print("Creating instance A on an older managed image pin...")
        inst.run_ok(
            "create", "-i", a, "-w", "main",
            "-n", host_a, "-p", inst.work_dir,
            "--orchestrator", "kubernetes",
            "--skip-argocd-install", "--skip-tls",
            "--canasta-image", "%s:%s" % (repo, pinned),
        )
        print("Creating instance B on a custom image...")
        inst_b.run_ok(
            "create", "-i", b, "-w", "main",
            "-n", host_b, "-p", inst_b.work_dir,
            "--orchestrator", "kubernetes",
            "--skip-argocd-install", "--skip-tls",
            "--canasta-image", "%s:latest" % repo,
        )

        output = inst.run_quiet("list")
        assert a in output and b in output, (
            "both instances should be listed:\n%s" % output
        )

        print("Verifying each instance has its own namespace and host...")
        assert _k8s_ingress_hosts(a) == {host_a}, _k8s_ingress_hosts(a)
        assert _k8s_ingress_hosts(b) == {host_b}, _k8s_ingress_hosts(b)
        for inst_id in (a, b):
            _k8s_wait_web(inst_id)
            _k8s_wiki_answers(inst_id)
        shared = other_workloads()
        print("  Shared workloads: %s" % ", ".join(sorted(shared)))

        print("Upgrading every instance on the controller...")
        inst.run_ok("upgrade")

        print("Verifying A moved to the current image and rolled...")
        values_a = _k8s_values(inst)
        assert values_a.get("image", {}).get("tag") == current, (
            "A's values.yaml image.tag should be bumped to %s, got %r"
            % (current, values_a.get("image"))
        )
        _k8s_wait_web(a)
        assert _k8s_web_image(a) == "%s:%s" % (repo, current), (
            "A's web Deployment is on %s" % _k8s_web_image(a)
        )
        assert _k8s_running_web_images(a) == {"%s:%s" % (repo, current)}, (
            "A's running web pods were not rolled onto the new image: %s"
            % _k8s_running_web_images(a)
        )
        _k8s_wiki_answers(a)

        print("Verifying B kept its custom image...")
        values_b = _k8s_values(inst_b)
        assert values_b.get("image", {}).get("tag") == "latest", (
            "upgrade must not repin a custom image; B's image is %r"
            % values_b.get("image")
        )
        assert _k8s_web_image(b) == "%s:latest" % repo, (
            "B's web Deployment is on %s" % _k8s_web_image(b)
        )
        _k8s_wiki_answers(b)

        before_b = _k8s_running_pods(b)
        assert before_b, "B has no Running pods"

        print("Stopping A...")
        inst.run_ok("stop", "-i", a)
        # stop scales A's workloads to zero; the pods then take a grace
        # period to terminate.
        deadline = time.time() + 180
        while _k8s_running_pods(a) and time.time() < deadline:
            time.sleep(5)
        assert not _k8s_running_pods(a), (
            "A still has Running pods 180s after stop: %s"
            % _k8s_running_pods(a)
        )
        assert _k8s_running_pods(b) == before_b, (
            "stopping A disturbed B's pods: before %s, after %s"
            % (before_b, _k8s_running_pods(b))
        )

        print("Deleting A...")
        inst.run_ok("delete", "-i", a, "--yes")
        _k8s_wait_namespace_gone(ns_a)
        output = inst.run_quiet("list")
        assert a not in output, "A still listed after delete:\n%s" % output
        assert b in output, "B dropped from the registry:\n%s" % output

        print("Verifying B and the shared workloads are intact...")
        assert _k8s_running_pods(b) == before_b, (
            "deleting A disturbed B's pods: before %s, after %s"
            % (before_b, _k8s_running_pods(b))
        )
        _k8s_wiki_answers(b)
        missing = shared - other_workloads()
        assert not missing, (
            "deleting A removed shared workloads: %s" % sorted(missing)
        )

        print("Deleting B...")
        inst_b.run_ok("delete", "-i", b, "--yes")
        _k8s_wait_namespace_gone(ns_b)
    finally:
        # B shares A's config dir, which A's cleanup removes; only B's
        # instance and work dir are B's to tear down.
        if inst_b.is_registered():
            inst_b.run("delete", "-i", b, "--yes")
        subprocess.run(
            ["sudo", "rm", "-rf", inst_b.work_dir], capture_output=True,
        )


def test_k8s_farm(inst):
    """Kubernetes farm, extension and scale paths on one instance.

    Adding a wiki on its own host must add that host to the Ingress.
    Enabling an extension must reach the running pods without an
    operator restart. Scaling the web tier must keep the Ingress hosts,
    which come from the derived domains overlay, not values.yaml.
    """
    _k8s_require_cluster()
    inst_id = inst.id

    print("Creating Kubernetes instance...")
    inst.run_ok(
        "create", "-i", inst_id, "-w", "main",
        "-n", "localhost", "-p", inst.work_dir,
        "--orchestrator", "kubernetes",
        "--skip-argocd-install",
    )
    _k8s_wait_web(inst_id)
    _k8s_wiki_answers(inst_id)
    assert _k8s_ingress_hosts(inst_id) == {"localhost"}, (
        _k8s_ingress_hosts(inst_id)
    )

    print("Adding a second wiki on its own host...")
    inst.run_ok(
        "add", "-i", inst_id, "-w", "second", "-u", "second.localhost",
    )
    hosts = _k8s_ingress_hosts(inst_id)
    assert hosts == {"localhost", "second.localhost"}, (
        "the Ingress should route both wiki hosts, got %s" % hosts
    )
    _k8s_wiki_answers(inst_id, "main")
    _k8s_wiki_answers(inst_id, "second")

    print("Enabling a bundled extension...")
    assert not _k8s_extension_loaded(inst_id, "main", "Cite"), (
        "precondition: Cite should not be loaded on a fresh instance"
    )
    inst.run_ok("extension", "enable", "-i", inst_id, "Cite")
    for wiki in ("main", "second"):
        assert _k8s_extension_loaded(inst_id, wiki, "Cite"), (
            "Cite is not loaded on %s after enable; the change did not "
            "reach the running pods" % wiki
        )

    print("Scaling the web tier to 2 replicas...")
    inst.run_ok("scale", "-i", inst_id, "--replicas", "2")
    ns = "canasta-%s" % inst_id
    replicas = _k8s_kubectl(
        "get", "deployment", "canasta-%s-web" % inst_id, "-n", ns,
        "-o", "jsonpath={.spec.replicas}/{.status.readyReplicas}",
    )
    assert replicas == "2/2", (
        "web should have 2 ready replicas, got spec/ready %s" % replicas
    )
    assert _k8s_values(inst).get("web", {}).get("replicaCount") == 2, (
        "scale should persist web.replicaCount to values.yaml"
    )
    assert _k8s_ingress_hosts(inst_id) == hosts, (
        "scale changed the Ingress hosts: %s -> %s"
        % (hosts, _k8s_ingress_hosts(inst_id))
    )
    _k8s_wiki_answers(inst_id, "second")


def test_k8s_uninstall(inst):
    """`uninstall k8s` removes the cluster and what followed it.

    NFS storage is set up first, so the uninstall has a default storage
    class and cluster-scoped exports to withdraw. Destroys the local
    cluster, so it runs only when CANASTA_TEST_ALLOW_K8S_UNINSTALL=1:
    CI sets it in a job of its own.
    """
    if os.environ.get("CANASTA_TEST_ALLOW_K8S_UNINSTALL") != "1":
        raise SkipTest(
            "destroys the local cluster; set "
            "CANASTA_TEST_ALLOW_K8S_UNINSTALL=1 to run"
        )
    _k8s_require_cluster()
    share = "/srv/nfs/canasta-uninstall-test"
    exports_file = "/etc/exports.d/canasta.exports"
    conf = os.path.join(inst.config_dir, "conf.json")

    def default_storage_class():
        try:
            with open(conf) as f:
                return (json.load(f).get("Settings") or {}).get(
                    "defaultStorageClass")
        except (OSError, ValueError):
            return None

    def exported():
        result = subprocess.run(
            ["sudo", "exportfs", "-v"], capture_output=True, text=True,
        )
        return result.stdout

    try:
        print("Setting up NFS storage with a local server...")
        inst.run_ok(
            "storage", "setup", "nfs", "--install-server", "--share", share,
        )
        assert default_storage_class() == "nfs", (
            "precondition: storage setup should record the default "
            "storage class, got %r" % default_storage_class()
        )
        assert share in exported(), (
            "precondition: the share should be exported:\n%s" % exported()
        )

        print("Uninstalling Kubernetes...")
        inst.run_ok("uninstall", "k8s", "-y")

        state = subprocess.run(
            ["systemctl", "is-active", "k3s"], capture_output=True, text=True,
        ).stdout.strip()
        assert state != "active", "the k3s unit is still active"
        assert not os.path.exists("/usr/local/bin/k3s-uninstall.sh"), (
            "the k3s uninstall script is still present"
        )
        assert not default_storage_class(), (
            "the default storage class was left in the registry: %r"
            % default_storage_class()
        )
        assert share not in exported(), (
            "the share is still exported after uninstall:\n%s" % exported()
        )
        assert not os.path.exists(exports_file), (
            "%s was left behind" % exports_file
        )
    finally:
        subprocess.run(["sudo", "rm", "-rf", share], capture_output=True)


K8S_TESTS = {
    "k8s-lifecycle": test_k8s_lifecycle,
    "k8s-build-from": test_k8s_build_from,
    "k8s-crowdsec": test_k8s_crowdsec,
    "k8s-crowdsec-rolling": test_k8s_crowdsec_rolling,
    "k8s-backup": test_k8s_backup,
    "k8s-gitops": test_k8s_gitops,
    "k8s-multi-instance": test_k8s_multi_instance,
    "k8s-farm": test_k8s_farm,
    "k8s-uninstall": test_k8s_uninstall,
}
