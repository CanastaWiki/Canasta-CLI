#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_DIR="${SCRIPT_DIR}/.ssh"
KEY_FILE="${KEY_DIR}/test_key"
CONTAINER_NAME="canasta-remote-test"
SSH_PORT=2222

echo "=== Setting up mock remote host ==="

# Build and start the container
echo "Building and starting container..."
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" up -d --build

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

# Add the docker group inside the container and add testuser to it so
# Docker CLI commands work via the mounted socket.
DOCKER_SOCK_GID="$(docker exec "${CONTAINER_NAME}" stat -c '%g' /var/run/docker.sock)"
docker exec "${CONTAINER_NAME}" bash -c \
    "groupadd -g ${DOCKER_SOCK_GID} dockerhost 2>/dev/null || true; usermod -aG dockerhost testuser"

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
