#!/usr/bin/env bash
# Remote host integration tests for Canasta-Ansible.
#
# These tests create a mock SSH host in a Docker container and run
# canasta commands against it to verify remote instance management.
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
SSHEOF

REMOTE_HOST="${SSH_USER}@${SSH_HOST}"

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
    # and logs for every container whose name starts with the instance
    # ID, so CI failures are diagnosable without a second round-trip.
    echo ""
    echo "=== Failure diagnostics ==="
    echo "--- docker ps -a ---"
    docker ps -a || true
    for c in $(docker ps -aq --filter "name=${INSTANCE_ID}"); do
        name=$(docker inspect --format='{{.Name}}' "$c" 2>/dev/null | sed 's:^/::')
        echo "--- ${name} health ---"
        docker inspect --format='{{json .State.Health}}' "$c" 2>/dev/null || true
        echo ""
        echo "--- ${name} logs (last 200) ---"
        docker logs --tail 200 "$c" 2>&1 || true
        echo ""
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
    if echo "${SCHED_OUTPUT}" | grep -q "0 4 \* \* \*" \
       && echo "${SCHED_OUTPUT}" | grep -q "snapshots older than 30d"; then
        pass "schedule list shows purge duration when --purge set"
    else
        fail "schedule list (with --purge) unexpected output: ${SCHED_OUTPUT}"
    fi
fi
echo ""

# --- Test 6: Delete without -H (resolved from registry) ---
echo "Test 6: canasta delete without -H"
if canasta delete -i "${INSTANCE_ID}" -y 2>&1; then
    pass "delete without -H"
else
    fail "delete without -H"
fi
echo ""

# --- Test 7: List (verify empty) ---
echo "Test 7: canasta list (should be empty)"
LIST_OUTPUT=$(canasta list 2>&1) || true
if echo "${LIST_OUTPUT}" | grep -q "${INSTANCE_ID}"; then
    fail "list still shows deleted instance (output: ${LIST_OUTPUT})"
else
    pass "list is empty after delete"
fi
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
