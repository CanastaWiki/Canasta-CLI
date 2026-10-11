# Remote host integration tests

These tests exercise `canasta` against remote hosts over SSH, using two
Docker containers as mock hosts. They run in CI as the `remote-integration`
job in `.github/workflows/integration-tests.yml`.

## Running

```bash
tests/integration/remote/run_tests.sh
```

This needs Linux with native Docker and the repo's `.venv` with Ansible
installed. It does not work with Docker Desktop on macOS, which cannot
bind-mount paths from inside sibling containers.

`run_tests.sh` calls `setup.sh` before the tests and `teardown.sh` on exit,
so neither normally needs to be run by hand.

## Setup

`docker-compose.yml` builds two identical hosts from `Dockerfile`
(Ubuntu with sshd, Python 3, git, git-crypt, cron and the Docker CLI):

| Container               | SSH port | Alias used by the tests  |
|-------------------------|----------|--------------------------|
| `canasta-remote-test`   | 2222     | `canasta-test-remote`    |
| `canasta-remote-test-2` | 2223     | `canasta-test-remote-2`  |

Both mount the runner's Docker socket, so the Canasta instances they create
run as sibling containers on the runner. `setup.sh` bind-mounts a shared
data directory (`$CANASTA_TEST_DATA`, default `/tmp/canasta-test-data`) at
the same path on the runner and in each host, so bind mounts in the
instances' compose files resolve the same way everywhere. It also makes
that directory `testuser`'s home, generates an SSH key in `.ssh/`, and
installs it for `testuser`.

`run_tests.sh` writes an SSH config that maps the two aliases to the
mapped ports, points Ansible at it, and uses a temporary
`CANASTA_CONFIG_DIR` as the controller registry, so the tests never touch
the real one.

## What the tests cover

- On the first host: `create -H`, `list`, `add` and `delete` resolving the
  host from the registry, `backup schedule` set and list, and `config set`
  with a restart.
- Across both hosts: one instance on each, a backup create and restore,
  `gitops init` on one host and `gitops join` on the other with a change
  pushed from one and pulled on the other, then deletion of both.
