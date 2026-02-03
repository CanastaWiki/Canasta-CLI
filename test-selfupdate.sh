#!/bin/bash
set -e

# Test script for PR #172: canasta self-update and canasta upgrade --all
# Tests all items in the PR #172 test plan.

PASS=0
FAIL=0

check() {
    local description="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  PASS: $description"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $description"
        FAIL=$((FAIL + 1))
    fi
}

check_output() {
    local description="$1"
    local pattern="$2"
    local output="$3"
    if echo "$output" | grep -q "$pattern"; then
        echo "  PASS: $description"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $description"
        echo "        Expected pattern: $pattern"
        echo "        Got: $output"
        FAIL=$((FAIL + 1))
    fi
}

check_output_absent() {
    local description="$1"
    local pattern="$2"
    local output="$3"
    if echo "$output" | grep -q "$pattern"; then
        echo "  FAIL: $description"
        echo "        Unexpected pattern found: $pattern"
        echo "        Got: $output"
        FAIL=$((FAIL + 1))
    else
        echo "  PASS: $description"
        PASS=$((PASS + 1))
    fi
}

# ---------------------------------------------------------------
# Step 0: Build
# ---------------------------------------------------------------
echo "=== Step 0: Build ==="
make build

# ---------------------------------------------------------------
# Step 1: build.sh compiles successfully
# ---------------------------------------------------------------
echo ""
echo "=== Step 1: build.sh compiles ==="
check "build.sh compiles successfully" test -f build/canasta-$(go env GOOS)-$(go env GOARCH)

# ---------------------------------------------------------------
# Step 2: go vet passes
# ---------------------------------------------------------------
echo ""
echo "=== Step 2: go vet ==="
VET_OUTPUT=$(go vet ./... 2>&1) || true
if [ -z "$VET_OUTPUT" ]; then
    echo "  PASS: go vet ./... passes"
    PASS=$((PASS + 1))
else
    echo "  FAIL: go vet ./... passes"
    echo "        $VET_OUTPUT"
    FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------
# Step 3: canasta version shows "dev" for local builds
# ---------------------------------------------------------------
echo ""
echo "=== Step 3: canasta version ==="
VERSION_OUTPUT=$(canasta version 2>&1) || true
echo "  Output: $VERSION_OUTPUT"
check_output "canasta version shows 'dev' for local builds" "dev" "$VERSION_OUTPUT"
check_output "canasta version shows commit hash" "commit" "$VERSION_OUTPUT"
check_output "canasta version shows build time" "built" "$VERSION_OUTPUT"

# ---------------------------------------------------------------
# Step 4: canasta self-update --help shows usage
# ---------------------------------------------------------------
echo ""
echo "=== Step 4: canasta self-update --help ==="
HELP_OUTPUT=$(canasta self-update --help 2>&1)
echo "  Output: $HELP_OUTPUT"
check_output "self-update --help shows usage" "Update the Canasta CLI" "$HELP_OUTPUT"

# ---------------------------------------------------------------
# Step 5: canasta self-update queries GitHub API
# ---------------------------------------------------------------
echo ""
echo "=== Step 5: canasta self-update (dev build) ==="
# Dev build should warn about version and attempt download
SELFUPDATE_OUTPUT=$(canasta self-update 2>&1) || true
echo "  Output: $SELFUPDATE_OUTPUT"
check_output "self-update warns about dev build" "dev build" "$SELFUPDATE_OUTPUT"
# It should also mention downloading (since dev build can't match latest)
check_output "self-update attempts to download latest" "Downloading\|Already up to date" "$SELFUPDATE_OUTPUT"

# ---------------------------------------------------------------
# Step 6: canasta upgrade --all --dry-run
# ---------------------------------------------------------------
echo ""
echo "=== Step 6: canasta upgrade --all --dry-run ==="
UPGRADE_ALL_OUTPUT=$(canasta upgrade --all --dry-run 2>&1) || true
echo "  Output: $UPGRADE_ALL_OUTPUT"
# Should either iterate instances or report none found
check_output "upgrade --all --dry-run runs without crash" \
    "Upgrading instance\|No registered installations found\|instances successfully" \
    "$UPGRADE_ALL_OUTPUT"

# ---------------------------------------------------------------
# Step 7: canasta upgrade --all --id foo returns error
# ---------------------------------------------------------------
echo ""
echo "=== Step 7: canasta upgrade --all --id foo ==="
MUTUAL_OUTPUT=$(canasta upgrade --all --id foo 2>&1) || true
echo "  Output: $MUTUAL_OUTPUT"
check_output "upgrade --all --id returns mutual exclusion error" "cannot use --all with --id" "$MUTUAL_OUTPUT"

# ---------------------------------------------------------------
# Step 8: Verify VERSION ldflags works when set
# ---------------------------------------------------------------
echo ""
echo "=== Step 8: VERSION ldflags ==="
VERSION=v99.0.0-test ./build.sh
VERSIONED_OUTPUT=$(./build/canasta-$(go env GOOS)-$(go env GOARCH) version 2>&1) || true
echo "  Output: $VERSIONED_OUTPUT"
check_output "VERSION ldflags embeds version in binary" "v99.0.0-test" "$VERSIONED_OUTPUT"
# Rebuild without version to restore dev build
make build

# ---------------------------------------------------------------
# Step 9: install.sh --skip-checks skips dependency checks
# ---------------------------------------------------------------
echo ""
echo "=== Step 9: install.sh --skip-checks ==="
# Use -v with a valid version so choose_version doesn't prompt for input.
# The download will fail (no sudo tty), but we only care about the output
# before the download step.
SKIP_OUTPUT=$(bash install.sh --skip-checks -v 1.58.0 2>&1) || true
echo "  Output: $SKIP_OUTPUT"
check_output_absent "--skip-checks suppresses dependency checks" "Checking dependencies" "$SKIP_OUTPUT"
check_output "--skip-checks still proceeds to version check" "Checking the version" "$SKIP_OUTPUT"

# ---------------------------------------------------------------
# Step 10: install.sh default behavior still checks dependencies
# ---------------------------------------------------------------
echo ""
echo "=== Step 10: install.sh default (with deps) ==="
# Pass a valid -v so it doesn't prompt. Download will fail but we check
# that dependency checking runs.
DEFAULT_OUTPUT=$(bash install.sh -v 1.58.0 2>&1) || true
echo "  Output: $DEFAULT_OUTPUT"
check_output "default install.sh checks dependencies" "Checking dependencies\|dependencies are missing" "$DEFAULT_OUTPUT"

# ---------------------------------------------------------------
# Step 11: install.sh --target accepts custom path
# ---------------------------------------------------------------
echo ""
echo "=== Step 11: install.sh --target ==="
# Verify the flag is parsed (not rejected as unknown). Download will fail
# due to sudo, but the script should reach the download step.
TARGET_OUTPUT=$(bash install.sh --skip-checks --target /tmp/test-canasta -v 1.58.0 2>&1) || true
echo "  Output: $TARGET_OUTPUT"
check_output "--target is accepted (reaches download step)" "Downloading\|Detected platform" "$TARGET_OUTPUT"
# Ensure it didn't print the usage error (which would mean --target was rejected)
check_output_absent "--target is not rejected as unknown flag" "Usage:" "$TARGET_OUTPUT"

# ---------------------------------------------------------------
# Step 12: install.sh -l still lists versions
# ---------------------------------------------------------------
echo ""
echo "=== Step 12: install.sh -l ==="
LIST_OUTPUT=$(bash install.sh -l 2>&1) || true
echo "  Output (first 3 lines): $(echo "$LIST_OUTPUT" | head -3)"
check_output "install.sh -l lists versions" "Available versions" "$LIST_OUTPUT"

echo ""
echo "==============================="
echo "  Results: $PASS passed, $FAIL failed"
echo "==============================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
