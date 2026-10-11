#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_DIR="${SCRIPT_DIR}/.ssh"
KEY_FILE="${KEY_DIR}/test_key"
CONTAINER_NAME="canasta-remote-test"
SSH_PORT=2222
# The second mock host, used by the multi-host tests.
CONTAINER_NAME_2="canasta-remote-test-2"
SSH_PORT_2=2223

echo "=== Setting up mock remote hosts ==="

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
      # Mount at the SAME path so Docker bind mounts from the instance
      # resolve identically on both the host and inside this container.
      - ${CANASTA_TEST_DATA}:${CANASTA_TEST_DATA}
    environment:
      - HOME=${CANASTA_TEST_DATA}
  remote-host-2:
    volumes:
      - ${CANASTA_TEST_DATA}:${CANASTA_TEST_DATA}
    environment:
      - HOME=${CANASTA_TEST_DATA}
OVEOF

# Build and start the containers
echo "Building and starting containers..."
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" -f "${OVERRIDE}" up -d --build

# Generate SSH key pair
echo "Generating SSH key pair..."
mkdir -p "${KEY_DIR}"
rm -f "${KEY_FILE}" "${KEY_FILE}.pub"
ssh-keygen -t ed25519 -f "${KEY_FILE}" -N "" -q

prepare_container() {
    local container="$1"
    # Change testuser's home to the shared data path so sshd finds authorized_keys
    docker exec "${container}" usermod -d "${CANASTA_TEST_DATA}" testuser

    # Create .ssh directory in the shared data path. Also chown the parent
    # itself to testuser, otherwise sshd's StrictModes rejects the login
    # whenever the host-side user running these tests has a UID different
    # from testuser's container UID (1001) — the bind-mount preserves
    # numeric ownership, so e.g. host UID 1000 shows up inside the
    # container as a non-testuser owner of testuser's home directory.
    docker exec "${container}" bash -c \
        "mkdir -p ${CANASTA_TEST_DATA}/.ssh && chmod 700 ${CANASTA_TEST_DATA}/.ssh && chown testuser:testuser ${CANASTA_TEST_DATA}/.ssh ${CANASTA_TEST_DATA}"

    # Create canasta config directory
    docker exec "${container}" bash -c \
        "mkdir -p /etc/canasta && chown testuser:testuser /etc/canasta"

    # Copy public key into container
    echo "Installing public key in container..."
    docker cp "${KEY_FILE}.pub" "${container}:${CANASTA_TEST_DATA}/.ssh/authorized_keys"
    docker exec "${container}" chown testuser:testuser "${CANASTA_TEST_DATA}/.ssh/authorized_keys"
    docker exec "${container}" chmod 600 "${CANASTA_TEST_DATA}/.ssh/authorized_keys"

    # Make the Docker socket accessible to testuser.
    # On macOS the socket may have a different GID, so chmod is more reliable.
    if docker exec "${container}" test -S /var/run/docker.sock; then
        docker exec "${container}" chmod 666 /var/run/docker.sock
        echo "Docker socket made accessible."
    else
        echo "Warning: Docker socket not available in container"
    fi
}

prepare_container "${CONTAINER_NAME}"
prepare_container "${CONTAINER_NAME_2}"

# Wait for SSH to become available
wait_for_ssh() {
    local port="$1"
    echo "Waiting for SSH on port ${port}..."
    for i in $(seq 1 30); do
        if ssh -i "${KEY_FILE}" \
               -o StrictHostKeyChecking=no \
               -o UserKnownHostsFile=/dev/null \
               -o ConnectTimeout=2 \
               -p "${port}" \
               testuser@127.0.0.1 \
               "echo ok" >/dev/null 2>&1; then
            echo "SSH is ready on port ${port}."
            return 0
        fi
        sleep 1
    done
    echo "ERROR: SSH did not become available on port ${port} in time."
    exit 1
}
wait_for_ssh "${SSH_PORT}"
wait_for_ssh "${SSH_PORT_2}"

echo ""
echo "=== Mock remote hosts are running ==="
echo "  Containers: ${CONTAINER_NAME}, ${CONTAINER_NAME_2}"
echo "  SSH ports:  ${SSH_PORT}, ${SSH_PORT_2}"
echo "  User:       testuser"
echo "  Key file:   ${KEY_FILE}"
echo "  Connect:    ssh -i ${KEY_FILE} -o StrictHostKeyChecking=no -p ${SSH_PORT} testuser@127.0.0.1"
echo ""
