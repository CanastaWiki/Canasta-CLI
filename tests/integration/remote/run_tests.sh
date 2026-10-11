#!/usr/bin/env bash
# Remote host integration tests for the Canasta CLI.
#
# These tests create two mock SSH hosts in Docker containers and run
# canasta commands against them to verify remote instance management,
# including gitops and backups across hosts.
#
# Requirements:
#   - Linux host with native Docker (NOT Docker Desktop on macOS,
#     which can't mount paths from inside sibling containers)
#   - Python venv with Ansible installed (.venv/)
#
# Runs in CI on Linux runners. Not runnable on macOS with Docker Desktop.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# Use canasta.py directly with the repo's venv Python
CANASTA_PY="${REPO_ROOT}/canasta.py"
PYTHON="${REPO_ROOT}/.venv/bin/python"
if [ ! -x "${PYTHON}" ]; then
    echo "ERROR: Python venv not found at ${PYTHON}"
    echo "Run: cd ${REPO_ROOT} && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
canasta() {
    "${PYTHON}" "${CANASTA_PY}" "$@"
}
KEY_FILE="${SCRIPT_DIR}/.ssh/test_key"
SSH_PORT=2222
SSH_USER="testuser"
# Use a hostname alias that SSH config maps to localhost:2222
SSH_HOST="canasta-test-remote"
INSTANCE_ID="remote-test"
# The multi-host tests run one instance on each mock host.
SSH_PORT_2=2223
SSH_HOST_2="canasta-test-remote-2"
MH_ID_1="remote-mh-1"
MH_ID_2="remote-mh-2"
export CANASTA_TEST_DATA="${CANASTA_TEST_DATA:-/tmp/canasta-test-data}"

# Use a temporary config dir for test isolation
export CANASTA_CONFIG_DIR
CANASTA_CONFIG_DIR="$(mktemp -d)"

# Create SSH config that maps the test host alias to localhost:2222
SSH_CONFIG="${CANASTA_CONFIG_DIR}/ssh_config"
cat > "${SSH_CONFIG}" <<SSHEOF
Host ${SSH_HOST}
    HostName 127.0.0.1
    Port ${SSH_PORT}
    User ${SSH_USER}
    IdentityFile ${KEY_FILE}
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
Host ${SSH_HOST_2}
    HostName 127.0.0.1
    Port ${SSH_PORT_2}
    User ${SSH_USER}
    IdentityFile ${KEY_FILE}
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
SSHEOF

REMOTE_HOST="${SSH_USER}@${SSH_HOST}"
REMOTE_HOST_2="${SSH_USER}@${SSH_HOST_2}"

# Run a shell command on a mock host.
on_host() {
    ssh -F "${SSH_CONFIG}" "$1" "$2"
}

# Track results
PASS=0
FAIL=0
TESTS_RUN=0

pass() {
    TESTS_RUN=$((TESTS_RUN + 1))
    PASS=$((PASS + 1))
    echo "  PASS: $1"
}

fail() {
    TESTS_RUN=$((TESTS_RUN + 1))
    FAIL=$((FAIL + 1))
    echo "  FAIL: $1"
}

dump_failure_diagnostics() {
    # Only runs when a test has already failed. Dumps container state
    # and logs for every container whose name starts with a test instance
    # ID, so CI failures are diagnosable without a second round-trip.
    echo ""
    echo "=== Failure diagnostics ==="
    echo "--- docker ps -a ---"
    docker ps -a || true
    for id in "${INSTANCE_ID}" "${MH_ID_1}" "${MH_ID_2}"; do
        for c in $(docker ps -aq --filter "name=${id}"); do
            name=$(docker inspect --format='{{.Name}}' "$c" 2>/dev/null | sed 's:^/::')
            echo "--- ${name} health ---"
            docker inspect --format='{{json .State.Health}}' "$c" 2>/dev/null || true
            echo ""
            echo "--- ${name} logs (last 200) ---"
            docker logs --tail 200 "$c" 2>&1 || true
            echo ""
        done
    done
}

cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    # Surface container state/logs on failure before we tear anything down.
    if [ "${FAIL:-0}" -gt 0 ]; then
        dump_failure_diagnostics
    fi
    # Best-effort delete of instance if it still exists
    canasta delete -i "${INSTANCE_ID}" -y 2>/dev/null || true
    canasta delete -i "${MH_ID_1}" -y 2>/dev/null || true
    canasta delete -i "${MH_ID_2}" -y 2>/dev/null || true
    # Root-run backup containers write the restic repository, so remove it
    # from inside a mock host, where testuser can sudo.
    on_host "${REMOTE_HOST}" "sudo rm -rf '${CANASTA_TEST_DATA}/restic-${MH_ID_1}'" \
        2>/dev/null || true
    # Teardown the mock remote host
    "${SCRIPT_DIR}/teardown.sh"
    # Remove temp config dir
    rm -rf "${CANASTA_CONFIG_DIR}"
}
trap cleanup EXIT

# --- Setup ---
echo "=== Starting mock remote host ==="
"${SCRIPT_DIR}/setup.sh"

# Tell SSH and Ansible to use our test SSH config
export ANSIBLE_SSH_ARGS="-F ${SSH_CONFIG}"
export ANSIBLE_HOST_KEY_CHECKING=False

echo ""
echo "=== Running remote integration tests ==="
echo ""

# --- Test 1: Create with -H ---
echo "Test 1: canasta create with -H"
if canasta create -H "${REMOTE_HOST}" -i "${INSTANCE_ID}" -w main -n localhost -p "${CANASTA_TEST_DATA}" 2>&1; then
    pass "create with -H"
else
    fail "create with -H"
fi
echo ""

# --- Test 2: List (verify instance appears with correct status) ---
echo "Test 2: canasta list (instance should appear as RUNNING)"
LIST_OUTPUT=$(canasta list 2>&1) || true
if ! echo "${LIST_OUTPUT}" | grep -q "${INSTANCE_ID}"; then
    fail "list doesn't show remote instance (output: ${LIST_OUTPUT})"
elif echo "${LIST_OUTPUT}" | grep -q "NOT FOUND"; then
    fail "list shows NOT FOUND instead of RUNNING (output: ${LIST_OUTPUT})"
elif echo "${LIST_OUTPUT}" | grep -q "RUNNING"; then
    pass "list shows remote instance as RUNNING"
else
    fail "list shows unexpected status (output: ${LIST_OUTPUT})"
fi
echo ""

# --- Test 3: Add wiki without -H (resolved from registry) ---
echo "Test 3: canasta add without -H (host from registry)"
if canasta add -i "${INSTANCE_ID}" -w draft -u localhost/draft 2>&1; then
    pass "add wiki without -H"
else
    fail "add wiki without -H"
fi
echo ""

# --- Test 4: Backup schedule set/list without --purge ---
echo "Test 4: backup schedule list (no --purge)"
if ! canasta backup schedule set -i "${INSTANCE_ID}" "0 3 * * *" 2>&1; then
    fail "schedule set without --purge"
else
    SCHED_OUTPUT=$(canasta backup schedule list -i "${INSTANCE_ID}" 2>&1) || true
    if echo "${SCHED_OUTPUT}" | grep -q "0 3 \* \* \*" \
       && echo "${SCHED_OUTPUT}" | grep -qi "not configured"; then
        pass "schedule list shows 'not configured' when --purge absent"
    else
        fail "schedule list (no --purge) unexpected output: ${SCHED_OUTPUT}"
    fi
fi
echo ""

# --- Test 5: Backup schedule set/list with --purge ---
echo "Test 5: backup schedule list (with --purge)"
if ! canasta backup schedule set -i "${INSTANCE_ID}" "0 4 * * *" --purge-older-than 30d 2>&1; then
    fail "schedule set with --purge-older-than"
else
    SCHED_OUTPUT=$(canasta backup schedule list -i "${INSTANCE_ID}" 2>&1) || true
    # --purge-older-than is a spelling of --keep-within, and the schedule
    # reports the retention in restic's own flags.
    if echo "${SCHED_OUTPUT}" | grep -q "0 4 \* \* \*" \
       && echo "${SCHED_OUTPUT}" | grep -q -- "--keep-within 30d"; then
        pass "schedule list shows the retention flags when --purge set"
    else
        fail "schedule list (with --purge) unexpected output: ${SCHED_OUTPUT}"
    fi
fi
echo ""

# --- Test 5b: a multi-flag retention policy round-trips ---
# The reason the schedule reports flags rather than prose: a policy of
# several --keep-* flags has no single duration to name.
echo "Test 5b: backup schedule list (multi-flag retention)"
if ! canasta backup schedule set -i "${INSTANCE_ID}" "0 5 * * *" \
        --keep-daily 7 --keep-weekly 4 2>&1; then
    fail "schedule set with --keep-daily/--keep-weekly"
else
    SCHED_OUTPUT=$(canasta backup schedule list -i "${INSTANCE_ID}" 2>&1) || true
    if echo "${SCHED_OUTPUT}" | grep -q "0 5 \* \* \*" \
       && echo "${SCHED_OUTPUT}" | grep -q -- "--keep-daily 7" \
       && echo "${SCHED_OUTPUT}" | grep -q -- "--keep-weekly 4"; then
        pass "schedule list shows every flag of a multi-flag policy"
    else
        fail "schedule list (multi-flag) unexpected output: ${SCHED_OUTPUT}"
    fi
fi
echo ""

# --- Test 6: config set with restart against remote (multi-resolve_instance) ---
# Regression coverage for the "delegate_to: localhost after switch_connection
# leaks the SSH connection" failure mode that resolve_instance and delete
# deregistration originally fixed by replacing delegate_to
# with a save/switch/restore-connection dance. `canasta config set` without
# --no-restart is the simplest remote command that calls resolve_instance.yml
# twice in one play: once at the top of set.yml, then again from the restart
# it triggers at the end. The second call inherits ansible_connection=ssh
# from the first switch_connection.yml, and any future regression to bare
# delegate_to would route the registry lookup to the remote host instead of
# the controller. Symptoms: command exits non-zero, controller registry is
# corrupted, or the remote .env never reflects the change. The three
# assertions below catch each symptom independently.
echo "Test 6: canasta config set with restart against remote"
NEW_PHPVAR=2000
if canasta config set -i "${INSTANCE_ID}" "PHP_MAX_INPUT_VARS=${NEW_PHPVAR}" 2>&1; then
    pass "config set with restart succeeds"
else
    fail "config set with restart failed (regression in resolve_instance multi-call path)"
fi

LIST_OUTPUT=$(canasta list 2>&1) || true
if echo "${LIST_OUTPUT}" | grep -q "${INSTANCE_ID}"; then
    pass "registry still has instance after config set + restart"
else
    fail "registry lost instance after config set + restart (output: ${LIST_OUTPUT})"
fi

ENV_VALUE=$(ssh -F "${SSH_CONFIG}" "${REMOTE_HOST}" \
    "grep '^PHP_MAX_INPUT_VARS=' '${CANASTA_TEST_DATA}/${INSTANCE_ID}/.env'" \
    2>/dev/null | tail -n 1 | cut -d= -f2 | tr -d '\r\n ') || true
if [ "${ENV_VALUE}" = "${NEW_PHPVAR}" ]; then
    pass "remote .env reflects new PHP_MAX_INPUT_VARS"
else
    fail "remote .env PHP_MAX_INPUT_VARS is '${ENV_VALUE}' (expected ${NEW_PHPVAR})"
fi
echo ""

# --- Test 7: Delete without -H (resolved from registry) ---
# Also exercises the controller-deregistration path.
# If the registry write inside delete is routed to the remote (regression),
# Test 8's `canasta list` will still show the instance.
echo "Test 7: canasta delete without -H"
if canasta delete -i "${INSTANCE_ID}" -y 2>&1; then
    pass "delete without -H"
else
    fail "delete without -H"
fi
echo ""

# --- Test 8: List (verify empty) ---
echo "Test 8: canasta list (should be empty)"
LIST_OUTPUT=$(canasta list 2>&1) || true
if echo "${LIST_OUTPUT}" | grep -q "${INSTANCE_ID}"; then
    fail "list still shows deleted instance (output: ${LIST_OUTPUT})"
else
    pass "list is empty after delete"
fi
echo ""

# --- Multi-host tests: one instance on each of two remote hosts ---
# Both mock hosts share the runner's Docker daemon, so each instance gets
# its own ports through a controller-side env file.
MH_PATH_1="${CANASTA_TEST_DATA}/${MH_ID_1}"
MH_PATH_2="${CANASTA_TEST_DATA}/${MH_ID_2}"
MH_ENV_1="${CANASTA_CONFIG_DIR}/${MH_ID_1}.env"
MH_ENV_2="${CANASTA_CONFIG_DIR}/${MH_ID_2}.env"
printf 'HTTP_PORT=18080\nHTTPS_PORT=18443\nCADDY_AUTO_HTTPS=off\n' > "${MH_ENV_1}"
printf 'HTTP_PORT=18090\nHTTPS_PORT=18453\nCADDY_AUTO_HTTPS=off\n' > "${MH_ENV_2}"

# --- Test 9: create one instance on each host ---
echo "Test 9: canasta create on two remote hosts"
if canasta create -H "${REMOTE_HOST}" -i "${MH_ID_1}" -w main -n localhost \
        -p "${CANASTA_TEST_DATA}" -e "${MH_ENV_1}" 2>&1; then
    pass "create on host 1"
else
    fail "create on host 1"
fi
if canasta create -H "${REMOTE_HOST_2}" -i "${MH_ID_2}" -w main -n localhost \
        -p "${CANASTA_TEST_DATA}" -e "${MH_ENV_2}" 2>&1; then
    pass "create on host 2"
else
    fail "create on host 2"
fi
echo ""

# --- Test 10: remote backup create and restore ---
# The restic repository sits under the shared data path, which resolves to
# the same directory on the runner, where the backup containers run.
echo "Test 10: backup create and restore on a remote host"
MH_RESTIC_REPO="${CANASTA_TEST_DATA}/restic-${MH_ID_1}"
SENTINEL="${MH_PATH_1}/config/remote-backup-sentinel.txt"
if ! on_host "${REMOTE_HOST}" "mkdir -p '${MH_RESTIC_REPO}'" \
   || ! canasta config set -i "${MH_ID_1}" "RESTIC_REPOSITORY=${MH_RESTIC_REPO}" --no-restart 2>&1 \
   || ! canasta config set -i "${MH_ID_1}" "RESTIC_PASSWORD=testpass" --no-restart 2>&1 \
   || ! canasta backup init -i "${MH_ID_1}" 2>&1; then
    fail "backup repository setup on host 1"
elif ! on_host "${REMOTE_HOST}" "echo sentinel > '${SENTINEL}'" \
   || ! canasta backup create -i "${MH_ID_1}" -t remote-snap 2>&1; then
    fail "backup create on host 1"
else
    pass "backup create on host 1"
    on_host "${REMOTE_HOST}" "rm -f '${SENTINEL}'" || true
    if ! canasta backup restore -i "${MH_ID_1}" -s latest --skip-safety-backup 2>&1; then
        fail "backup restore on host 1"
    elif on_host "${REMOTE_HOST}" "test -f '${SENTINEL}'"; then
        pass "backup restore brings back a file deleted after the snapshot"
    else
        fail "backup restore did not bring back ${SENTINEL}"
    fi
fi
echo ""

# --- Test 11: gitops across two hosts ---
# The bare repository sits under the shared data path, so both hosts reach
# it at the same local path. The git-crypt key lives on the controller.
echo "Test 11: gitops init on host 1, join on host 2, push from 1, pull on 2"
MH_REPO="${CANASTA_TEST_DATA}/gitops-multihost.git"
MH_KEY="${CANASTA_CONFIG_DIR}/gitops-multihost.key"
MH_FILE="config/settings/global/MultiHostTest.php"
GIT_ID=(--git-user-name "CI" --git-user-email "ci@canasta.wiki")
if ! on_host "${REMOTE_HOST}" "git init --bare --initial-branch=main '${MH_REPO}'"; then
    fail "create the bare gitops repository"
elif ! canasta gitops init -i "${MH_ID_1}" -n host1 --repo "${MH_REPO}" \
        --key "${MH_KEY}" "${GIT_ID[@]}" 2>&1; then
    fail "gitops init on host 1"
elif ! canasta gitops join -i "${MH_ID_2}" -n host2 --repo "${MH_REPO}" \
        --key "${MH_KEY}" "${GIT_ID[@]}" 2>&1; then
    fail "gitops join on host 2"
else
    pass "gitops init on host 1 and join on host 2"
    # The join pushed host 2's entry, so host 1 pulls before it can push.
    if ! canasta gitops pull -i "${MH_ID_1}" 2>&1; then
        fail "gitops pull on host 1 after the join"
    elif on_host "${REMOTE_HOST}" "test -f '${MH_PATH_1}/hosts/host2/vars.yaml'"; then
        pass "host 1 sees host 2's entry after pulling"
    else
        fail "hosts/host2/vars.yaml missing on host 1 after pull"
    fi

    if ! on_host "${REMOTE_HOST}" \
            "echo '<?php \$wgMultiHostTest = true;' > '${MH_PATH_1}/${MH_FILE}'" \
       || ! canasta gitops add -i "${MH_ID_1}" "${MH_FILE}" 2>&1 \
       || ! canasta gitops push -i "${MH_ID_1}" 2>&1; then
        fail "gitops add and push on host 1"
    elif ! canasta gitops pull -i "${MH_ID_2}" 2>&1; then
        fail "gitops pull on host 2"
    elif on_host "${REMOTE_HOST_2}" "grep -q wgMultiHostTest '${MH_PATH_2}/${MH_FILE}'"; then
        pass "a change pushed from host 1 reaches host 2 on pull"
    else
        fail "${MH_FILE} missing on host 2 after pull"
    fi
fi
echo ""

# --- Test 12: delete both multi-host instances ---
echo "Test 12: canasta delete on both remote hosts"
for id in "${MH_ID_1}" "${MH_ID_2}"; do
    if canasta delete -i "${id}" -y 2>&1; then
        pass "delete ${id}"
    else
        fail "delete ${id}"
    fi
done
echo ""

# --- Summary ---
echo "==============================="
echo "  Tests run: ${TESTS_RUN}"
echo "  Passed:    ${PASS}"
echo "  Failed:    ${FAIL}"
echo "==============================="

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi
