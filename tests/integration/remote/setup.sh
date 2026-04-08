#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_DIR="${SCRIPT_DIR}/.ssh"
KEY_FILE="${KEY_DIR}/test_key"
CONTAINER_NAME="canasta-remote-test"
SSH_PORT=2222

echo "=== Setting up mock remote host ==="

# Create shared data directory on the host so sibling containers
# (Canasta instances started via Docker socket) can bind-mount from it.
export CANASTA_TEST_DATA="${CANASTA_TEST_DATA:-/tmp/canasta-test-data}"
mkdir -p "${CANASTA_TEST_DATA}"

# Generate a compose override that bind-mounts the shared directory
# into the SSH container's home. This keeps docker-compose.yml clean.
OVERRIDE="${SCRIPT_DIR}/docker-compose.testing.override.yml"
cat > "${OVERRIDE}" <<OVEOF
services:
  remote-host:
    volumes:
      - ${CANASTA_TEST_DATA}:/home/testuser
OVEOF

# Build and start the container
echo "Building and starting container..."
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" -f "${OVERRIDE}" up -d --build

# Create .ssh directory (the shared volume mount overwrites /home/testuser)
docker exec "${CONTAINER_NAME}" bash -c \
    "mkdir -p /home/testuser/.ssh && chmod 700 /home/testuser/.ssh && chown testuser:testuser /home/testuser/.ssh"

# Create canasta config directory
docker exec "${CONTAINER_NAME}" bash -c \
    "mkdir -p /etc/canasta && chown testuser:testuser /etc/canasta"

# Generate SSH key pair
echo "Generating SSH key pair..."
mkdir -p "${KEY_DIR}"
rm -f "${KEY_FILE}" "${KEY_FILE}.pub"
ssh-keygen -t ed25519 -f "${KEY_FILE}" -N "" -q

# Copy public key into container
echo "Installing public key in container..."
docker cp "${KEY_FILE}.pub" "${CONTAINER_NAME}:/home/testuser/.ssh/authorized_keys"
docker exec "${CONTAINER_NAME}" chown testuser:testuser /home/testuser/.ssh/authorized_keys
docker exec "${CONTAINER_NAME}" chmod 600 /home/testuser/.ssh/authorized_keys

# Make the Docker socket accessible to testuser.
# On macOS the socket may have a different GID, so chmod is more reliable.
if docker exec "${CONTAINER_NAME}" test -S /var/run/docker.sock; then
    docker exec "${CONTAINER_NAME}" chmod 666 /var/run/docker.sock
    echo "Docker socket made accessible."
else
    echo "Warning: Docker socket not available in container"
fi

# Wait for SSH to become available
echo "Waiting for SSH..."
for i in $(seq 1 30); do
    if ssh -i "${KEY_FILE}" \
           -o StrictHostKeyChecking=no \
           -o UserKnownHostsFile=/dev/null \
           -o ConnectTimeout=2 \
           -p "${SSH_PORT}" \
           testuser@127.0.0.1 \
           "echo ok" >/dev/null 2>&1; then
        echo "SSH is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: SSH did not become available in time."
        exit 1
    fi
    sleep 1
done

echo ""
echo "=== Mock remote host is running ==="
echo "  Container:  ${CONTAINER_NAME}"
echo "  SSH port:   ${SSH_PORT}"
echo "  User:       testuser"
echo "  Key file:   ${KEY_FILE}"
echo "  Connect:    ssh -i ${KEY_FILE} -o StrictHostKeyChecking=no -p ${SSH_PORT} testuser@127.0.0.1"
echo ""
