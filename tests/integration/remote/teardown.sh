#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_DIR="${SCRIPT_DIR}/.ssh"

echo "=== Tearing down mock remote host ==="

# Stop and remove the container
echo "Stopping container..."
OVERRIDE="${SCRIPT_DIR}/docker-compose.testing.override.yml"
if [ -f "${OVERRIDE}" ]; then
    docker compose -f "${SCRIPT_DIR}/docker-compose.yml" -f "${OVERRIDE}" down -v --remove-orphans 2>/dev/null || true
    rm -f "${OVERRIDE}"
else
    docker compose -f "${SCRIPT_DIR}/docker-compose.yml" down -v --remove-orphans 2>/dev/null || true
fi

# Clean up SSH keys
echo "Cleaning up SSH keys..."
rm -rf "${KEY_DIR}"

# Clean up shared data directory
CANASTA_TEST_DATA="${CANASTA_TEST_DATA:-/tmp/canasta-test-data}"
if [ -d "${CANASTA_TEST_DATA}" ]; then
    echo "Cleaning up shared data..."
    rm -rf "${CANASTA_TEST_DATA}"
fi

echo "=== Teardown complete ==="
