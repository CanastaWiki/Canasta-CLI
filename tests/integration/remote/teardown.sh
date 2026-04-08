#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_DIR="${SCRIPT_DIR}/.ssh"

echo "=== Tearing down mock remote host ==="

# Stop and remove the container
echo "Stopping container..."
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" down -v --remove-orphans 2>/dev/null || true

# Clean up SSH keys
echo "Cleaning up SSH keys..."
rm -rf "${KEY_DIR}"

echo "=== Teardown complete ==="
