# Mock-Remote Integration Test Design

## Overview

Integration tests for verifying Canasta-Ansible's remote host management
(`-H` / `--host`) using a Docker container as a simulated remote host.
This avoids the need for real remote infrastructure during CI/CD.

## Architecture

```
┌─────────────────────┐          SSH (port 2222)         ┌─────────────────────────┐
│   Test Runner        │ ──────────────────────────────► │   Remote Container       │
│   (host / CI)        │                                  │   (canasta-remote-test)  │
│                      │                                  │                          │
│   - pytest           │                                  │   - sshd                 │
│   - run_tests.py     │                                  │   - Docker (DinD or      │
│   - Ansible          │                                  │     socket mount)        │
│                      │                                  │   - Python 3             │
└─────────────────────┘                                  └─────────────────────────┘
```

## Remote Container Requirements

The "remote host" Docker container must provide:

1. **SSH server** (OpenSSH) listening on a mapped port (e.g., 2222 on the host).
2. **Docker Engine** -- either Docker-in-Docker (DinD, `--privileged`) or the
   host Docker socket bind-mounted (`-v /var/run/docker.sock:/var/run/docker.sock`).
   DinD is preferred for isolation.
3. **Python 3** -- required by Ansible for remote module execution.
4. **Canasta directory structure** -- `/etc/canasta/` writable by the test user.

### Dockerfile sketch

```dockerfile
FROM docker:24-dind

RUN apk add --no-cache openssh python3 bash sudo && \
    ssh-keygen -A && \
    adduser -D canasta && \
    echo "canasta ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

COPY entrypoint.sh /entrypoint.sh
EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
```

The entrypoint starts both `dockerd` (background) and `sshd` (foreground).

## SSH Key Setup

1. During test setup (`conftest.py` or `docker-compose.yml`), generate an
   ephemeral SSH key pair:
   ```bash
   ssh-keygen -t ed25519 -f /tmp/test_key -N ""
   ```
2. Copy the public key into the remote container's
   `/home/canasta/.ssh/authorized_keys`.
3. Configure Ansible inventory to use:
   ```ini
   [remote]
   remote-test ansible_host=127.0.0.1 ansible_port=2222 \
       ansible_user=canasta ansible_ssh_private_key_file=/tmp/test_key \
       ansible_ssh_common_args="-o StrictHostKeyChecking=no"
   ```

## Test Scenarios

### 1. Create with -H

```bash
./canasta -H canasta@127.0.0.1:2222 create -i remotesite -w main
```

Verify:
- Instance registered in local `conf.json` with `host: "canasta@127.0.0.1"`
- Docker Compose stack running on the remote container
- Instance directory exists on the remote container

### 2. List with -H

```bash
./canasta -H 127.0.0.1:2222 list
```

Verify:
- Output includes only instances on the specified host
- Instances on other hosts (or localhost) are excluded

### 3. Add wiki

```bash
./canasta add -i remotesite -w docs
```

Verify:
- Host resolved from registry (no `-H` needed)
- `wikis.yaml` on the remote container updated
- MediaWiki install.php ran successfully on the remote container

### 4. Delete

```bash
./canasta delete -i remotesite --yes
```

Verify:
- Docker Compose stack stopped and removed on the remote container
- Instance removed from local `conf.json`
- Instance directory removed from the remote container

### 5. Upgrade

```bash
./canasta upgrade -i remotesite
```

Verify:
- Docker image pulled on the remote container
- Containers restarted with new image
- maintenance/update.php ran successfully

## Integration with run_tests.py

The existing `tests/integration/run_tests.py` framework can be extended:

1. Add a `--remote` flag to `run_tests.py` that spins up the remote container
   before tests and tears it down after.
2. Create a new Molecule scenario `tests/integration/molecule/remote/` with:
   - `molecule.yml` -- defines the remote container as a platform
   - `converge.yml` -- runs the test scenarios above
   - `verify.yml` -- assertions
3. Alternatively, use a `docker-compose.test.yml` at
   `tests/integration/` that defines the remote container, and
   have `run_tests.py --remote` call `docker compose up -d` before
   invoking pytest.

### Suggested pytest structure

```
tests/integration/
    remote/
        conftest.py          # Fixture: start/stop remote container, generate SSH keys
        test_remote_create.py
        test_remote_list.py
        test_remote_add.py
        test_remote_delete.py
        test_remote_upgrade.py
        docker-compose.yml   # Remote container definition
        Dockerfile.remote    # Remote container image
```

Each test module uses a session-scoped fixture from `conftest.py` that:
1. Builds and starts the remote container
2. Generates SSH keys and injects the public key
3. Waits for SSH to become available
4. Yields a `RemoteHost` dataclass with connection details
5. Tears down the container on session end

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `CANASTA_TEST_REMOTE` | Enable remote tests | unset (skip) |
| `CANASTA_TEST_REMOTE_PORT` | SSH port on host | 2222 |
| `CANASTA_CONFIG_DIR` | Override config dir for test isolation | (temp dir) |

## Limitations and Notes

- DinD requires `--privileged`, which may not be available in all CI
  environments. The socket-mount approach is an alternative but provides
  less isolation.
- These tests are slower than unit tests (container startup, SSH handshake,
  Docker operations). They should be in a separate pytest mark
  (`@pytest.mark.remote`) and excluded from the default test run.
- Port conflicts: use dynamic port allocation or a fixed high port.
- This is a design document only. Implementation is tracked separately.
