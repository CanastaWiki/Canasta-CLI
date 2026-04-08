#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CANASTA="${REPO_ROOT}/canasta-native"
KEY_FILE="${SCRIPT_DIR}/.ssh/test_key"
SSH_PORT=2222
SSH_USER="testuser"
SSH_HOST="127.0.0.1"
REMOTE_HOST="${SSH_USER}@${SSH_HOST}:${SSH_PORT}"
INSTANCE_ID="remote-test"

# Use a temporary config dir for test isolation
export CANASTA_CONFIG_DIR
CANASTA_CONFIG_DIR="$(mktemp -d)"

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

cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    # Best-effort delete of instance if it still exists
    "${CANASTA}" delete -i "${INSTANCE_ID}" -y 2>/dev/null || true
    # Teardown the mock remote host
    "${SCRIPT_DIR}/teardown.sh"
    # Remove temp config dir
    rm -rf "${CANASTA_CONFIG_DIR}"
}
trap cleanup EXIT

# --- Setup ---
echo "=== Starting mock remote host ==="
"${SCRIPT_DIR}/setup.sh"

# Export SSH key so Ansible picks it up
export ANSIBLE_SSH_ARGS="-i ${KEY_FILE} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

echo ""
echo "=== Running remote integration tests ==="
echo ""

# --- Test 1: Create with -H ---
echo "Test 1: canasta create with -H"
if "${CANASTA}" -H "${REMOTE_HOST}" create -i "${INSTANCE_ID}" -w main -n localhost 2>&1; then
    pass "create with -H"
else
    fail "create with -H"
fi
echo ""

# --- Test 2: List (verify instance appears) ---
echo "Test 2: canasta list (instance should appear)"
LIST_OUTPUT=$("${CANASTA}" list 2>&1) || true
if echo "${LIST_OUTPUT}" | grep -q "${INSTANCE_ID}"; then
    pass "list shows remote instance"
else
    fail "list shows remote instance (output: ${LIST_OUTPUT})"
fi
echo ""

# --- Test 3: Add wiki without -H (resolved from registry) ---
echo "Test 3: canasta add without -H (host from registry)"
if "${CANASTA}" add -i "${INSTANCE_ID}" -w draft 2>&1; then
    pass "add wiki without -H"
else
    fail "add wiki without -H"
fi
echo ""

# --- Test 4: Delete without -H (resolved from registry) ---
echo "Test 4: canasta delete without -H"
if "${CANASTA}" delete -i "${INSTANCE_ID}" -y 2>&1; then
    pass "delete without -H"
else
    fail "delete without -H"
fi
echo ""

# --- Test 5: List (verify empty) ---
echo "Test 5: canasta list (should be empty)"
LIST_OUTPUT=$("${CANASTA}" list 2>&1) || true
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
