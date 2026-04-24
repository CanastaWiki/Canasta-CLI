# Canasta-Ansible

Ansible-based management tool for [Canasta](https://canasta.wiki) MediaWiki installations. Full feature parity with [Canasta-CLI](https://github.com/CanastaWiki/Canasta-CLI) (Go), plus multi-host management.

## Features

- **60 commands** covering instance lifecycle, wiki management, configuration, extensions, skins, maintenance, backup/restore, gitops, devmode, sitemaps, storage provisioning, and more
- **Docker Compose and Kubernetes** (Helm + Argo CD) orchestrator support
- **Multi-host management** from a single controller node via SSH
- **Multi-node Kubernetes** with configurable PVC access modes, multi-replica web pods, and ConfigMap-based config sync
- **Multi-host GitOps** with git-crypt encrypted per-host secrets, template rendering, and dev → staging → prod promotion
- **Auto-generated documentation** from a single command definitions file
- **328 unit tests** + Docker Compose, Kubernetes, GitOps, and remote-host integration tests in CI
- **Zero-migration compatibility** with existing Canasta-CLI installations (reads the same `conf.json` registry)

## Requirements

### Controller node

| Requirement | Purpose |
|------------|---------|
| Python 3.9+ | Ansible runtime |
| ansible-core 2.15+ | Playbook execution |
| Git | Playbook auto-update during upgrade |

### Target host (Docker Compose)

| Requirement | Purpose |
|------------|---------|
| Python 3 | Ansible module execution |
| Docker + Docker Compose v2 | Container orchestration |
| SSH server | Remote management (not needed if controller = target) |
| User in `www-data` group | Write access to container-created directories (`sudo usermod -aG www-data $USER`, then log out and back in) |

### Target host (Kubernetes)

| Requirement | Purpose |
|------------|---------|
| Python 3 + `kubernetes` library | Ansible module execution |
| kubectl (configured with cluster access) | Cluster management |
| Helm 3.10+ | Chart deployment |
| SSH server | Remote management (not needed if controller = target) |

For k3s: the kubeconfig at `/etc/rancher/k3s/k3s.yaml` must be
readable by the Ansible user (`sudo chmod 644 /etc/rancher/k3s/k3s.yaml`).

Optional Tools (depending on features used):

| Tool | Purpose |
|------------|---------|
| Argo CD | Kubernetes GitOps reconciliation |
| git + git-crypt | GitOps commands (Compose) |
| rsync | Remote backup/restore |

Run `canasta doctor` to verify all dependencies on a target host.

## Installation

### Option 1: Docker (recommended)

Only Docker is required. No Python, no Ansible.

```bash
sudo curl -o /usr/local/bin/canasta-docker \
  https://raw.githubusercontent.com/CanastaWiki/Canasta-Ansible/main/canasta-docker
sudo chmod +x /usr/local/bin/canasta-docker
sudo ln -sf /usr/local/bin/canasta-docker /usr/local/bin/canasta
```

The wrapper automatically pulls the `canasta-ansible` image on first
run and whenever `canasta upgrade` is invoked.

### Option 2: Native install

Run the installer:

```bash
curl -fsSL https://get.canasta.wiki | bash -s -- --native
```

Log out and back in for group membership to take effect.

<details>
<summary>What the installer does (for manual installs)</summary>

```bash
# System prerequisites
sudo apt-get install -y git python3 python3-venv

# Create a 'canasta' group so non-root users can write to the install dir
sudo groupadd --system canasta
sudo usermod -aG canasta "$USER"

# Clone and set group-writable permissions
sudo git clone https://github.com/CanastaWiki/Canasta-Ansible.git /opt/canasta-ansible
sudo chgrp -R canasta /opt/canasta-ansible
sudo chmod -R g+w /opt/canasta-ansible

# Mark as safe for git (needed because git refuses operations on
# directories owned by a different user)
sudo git config --system --add safe.directory /opt/canasta-ansible

# Python venv and Ansible collections (collections go to a
# system-wide path so all users can see them)
sudo python3 -m venv /opt/canasta-ansible/.venv
sudo /opt/canasta-ansible/.venv/bin/pip install -r /opt/canasta-ansible/requirements.txt
sudo /opt/canasta-ansible/.venv/bin/ansible-galaxy collection install \
  -r /opt/canasta-ansible/requirements.yml \
  -p /usr/share/ansible/collections
sudo chgrp -R canasta /opt/canasta-ansible/.venv
sudo chmod -R g+w /opt/canasta-ansible/.venv

# Build metadata and symlinks
sudo make -C /opt/canasta-ansible build-info
sudo ln -sf /opt/canasta-ansible/canasta-native /usr/local/bin/canasta-native
sudo ln -sf /usr/local/bin/canasta-native /usr/local/bin/canasta
```

</details>

### Both wrappers available

Whichever option you chose, both wrappers can coexist at
`/usr/local/bin`. If you installed via Docker, you can also set up
native as a backup (or vice versa). Switch the default at any time:

```bash
sudo ln -sf /usr/local/bin/canasta-docker /usr/local/bin/canasta   # use Docker
sudo ln -sf /usr/local/bin/canasta-native /usr/local/bin/canasta   # use native
```

### Remote hosts (both options)

The default inventory manages the local machine. For remote hosts, copy the example and add your targets:

```bash
# Native install only:
sudo cp /opt/canasta-ansible/inventory/hosts.yml.example \
  /opt/canasta-ansible/inventory/hosts.yml
# Edit hosts.yml to add remote hosts, then update ansible.cfg:
#   inventory = inventory/hosts.yml
```

### Verify

```bash
canasta version
canasta doctor    # checks dependencies on the target host
```

### Configuration directory

The instance registry (`conf.json`) is stored in a platform-specific
location:

- Linux (non-root): `$XDG_CONFIG_HOME/canasta/` or `~/.config/canasta/`
- Linux (root): `/etc/canasta/`
- macOS: `~/Library/Application Support/canasta/`

Override with `CANASTA_CONFIG_DIR`:

```bash
export CANASTA_CONFIG_DIR=/path/to/config
canasta list
```

## Quick Start

### Single node (controller = target)

```bash
# Create an instance
./canasta create --id mysite --wiki main --domain-name example.com

# Check status
./canasta list

# Manage the instance
./canasta start --id mysite
./canasta stop --id mysite
./canasta restart --id mysite

# Delete the instance
./canasta delete --id mysite --yes
```

### Multi-host management

The simplest way to target a remote host is the `user@host` shorthand:

```bash
canasta create --host ubuntu@prod1.example.com --id wiki-prod --wiki main
```

This works without any inventory setup. SSH keys must be in place for
the target user on the target host. New host keys are automatically
accepted on first connection.

**Saved hosts** — for hosts you use repeatedly, save them with the
`canasta host` commands:

```bash
# Add a host (stored in $CANASTA_CONFIG_DIR/hosts.yml)
canasta host add --name prod1 --ssh ubuntu@prod1.example.com

# With a custom Python interpreter
canasta host add --name prod2 --ssh canasta@10.0.0.5 --python /usr/bin/python3

# List saved hosts
canasta host list

# Remove a host
canasta host remove --name prod1
```

After adding, use the short name:

```bash
canasta create --host prod1 --id wiki-prod --wiki main
```

You can also edit `$CANASTA_CONFIG_DIR/hosts.yml` directly for
advanced options (SSH port, jump host, etc.). The file uses standard
Ansible inventory format:

```yaml
all:
  hosts:
    prod1:
      ansible_host: prod1.example.com
      ansible_user: canasta
      ansible_python_interpreter: /usr/bin/python3
      ansible_port: 2222
```

Manage instances across hosts:

```bash
# Create on a remote host
./canasta create --host prod1.example.com --id wiki-prod --wiki main

# Create on another host
./canasta create --host staging.example.com --id wiki-staging --wiki main

# List all instances across all hosts
./canasta list

# Upgrade all instances on all hosts
./canasta upgrade

```

### Kubernetes deployment

Canasta supports Kubernetes via Helm. The same CLI commands work
regardless of orchestrator — the underlying mechanics differ.
Optionally, Argo CD can be used for GitOps-driven continuous
reconciliation.

**macOS (local development):**

Enable Kubernetes in Docker Desktop (Settings → Kubernetes → Enable), then:

```bash
./canasta create --id mysite --wiki main --domain-name localhost \
  --orchestrator kubernetes --skip-argocd-install
```

**Linux server / VPS (production):**

k3s installs a full Kubernetes cluster as a single binary:

```bash
./canasta create --id mysite --wiki main --domain-name example.com \
  --orchestrator kubernetes --install-k3s
```

**WSL2:**

Same as Linux — WSL2 runs a real Linux kernel, so k3s works natively:

```bash
./canasta create --id mysite --wiki main --domain-name localhost \
  --orchestrator kubernetes --install-k3s
```

**Managed clusters (EKS, GKE, AKS):**

Provision the cluster externally, configure kubectl, then:

```bash
./canasta create --id mysite --wiki main --domain-name example.com \
  --orchestrator kubernetes --ingress-class nginx
```

Pass `--skip-argocd-install` if the cluster already has Argo CD.

**TLS with Let's Encrypt:**

When a real domain name is provided (not `localhost`), cert-manager
is automatically installed and Let's Encrypt certificates are
configured. Optionally pass `--tls-email` for certificate expiry
notifications.

**Multi-node shared storage:**

For multi-node clusters with multiple web replicas, set up a shared
StorageClass (NFS, EFS, etc.) and pass `--access-mode ReadWriteMany`:

```bash
canasta storage setup nfs --server 10.0.0.1 --share /srv/nfs/canasta
canasta create --id mysite --wiki main --domain-name example.com \
  --orchestrator kubernetes --storage-class nfs --access-mode ReadWriteMany
```

Then scale web replicas by adding `web: { replicaCount: 3 }` to the
instance's `values.yaml` and running `canasta restart`.

See [docs/multi-node.md](docs/multi-node.md) for the full
walkthrough including cluster setup, storage provisioning, scaling,
and known limitations.

## Commands

### Instance lifecycle

| Command | Description |
|---------|-------------|
| `canasta create` | Create a new Canasta instance |
| `canasta delete` | Delete a Canasta instance |
| `canasta start` | Start containers |
| `canasta stop` | Stop containers |
| `canasta restart` | Restart containers |
| `canasta list` | List all registered instances |
| `canasta version` | Display version information |
| `canasta doctor` | Check target host dependencies |
| `canasta upgrade` | Upgrade all instances (auto-pulls latest playbooks) |

### Wiki management

| Command | Description |
|---------|-------------|
| `canasta add` | Add a wiki to an instance |
| `canasta remove` | Remove a wiki |
| `canasta import` | Import a database |
| `canasta export` | Export a database |

### Configuration

| Command | Description |
|---------|-------------|
| `canasta config get` | Show settings |
| `canasta config set` | Change settings |
| `canasta config unset` | Remove settings |

### Extensions & Skins

| Command | Description |
|---------|-------------|
| `canasta extension list\|enable\|disable` | Manage extensions |
| `canasta skin list\|enable\|disable` | Manage skins |

### Maintenance

| Command | Description |
|---------|-------------|
| `canasta maintenance update` | Run update.php |
| `canasta maintenance script` | Run maintenance scripts |
| `canasta maintenance extension` | Run extension scripts |
| `canasta maintenance exec` | Execute commands in container |
| `canasta sitemap generate\|remove` | Manage sitemaps |

### Backup & Restore

| Command | Description |
|---------|-------------|
| `canasta backup init\|create\|restore\|list\|delete` | Backup operations |
| `canasta backup check\|diff\|files\|purge\|unlock` | Maintenance |
| `canasta backup schedule set\|list\|remove` | Scheduled backups |

### GitOps

| Command | Description |
|---------|-------------|
| `canasta gitops init\|join` | Set up git-based config management |
| `canasta gitops add\|rm\|push\|pull\|status\|diff` | Manage config |
| `canasta gitops fix-submodules` | Fix submodule registration (Compose) |
| `canasta gitops sync` | Trigger Argo CD sync (Kubernetes) |

### Storage provisioning (Kubernetes)

| Command | Description |
|---------|-------------|
| `canasta storage setup nfs` | Install NFS CSI driver + StorageClass |
| `canasta storage setup efs` | Install AWS EFS CSI driver + StorageClass |

### Host management

| Command | Description |
|---------|-------------|
| `canasta host add --name NAME --ssh user@host` | Save a host definition for `--host NAME` |
| `canasta host list` | List saved hosts |
| `canasta host remove --name NAME` | Remove a saved host |

### Development

| Command | Description |
|---------|-------------|
| `canasta devmode enable\|disable` | Toggle development mode (xdebug, Compose only) |

### Global flags

| Flag | Description |
|------|-------------|
| `--host, -H HOST` | Target host (default: localhost) |
| `--verbose, -v` | Show Ansible task output |
| `--help, -h` | Show help |

Run `canasta <command> --help` for command-specific flags.

## Architecture

```
./canasta (bash) → canasta.py (argparse CLI) → ansible-playbook canasta.yml
  └── playbooks/<command>.yml
        └── roles/<role>/tasks/<action>.yml
              └── ansible.builtin.* modules + custom Python modules
```

- **`meta/command_definitions.yml`** -- single source of truth for all commands, parameters, and documentation
- **`canasta.py`** -- Python CLI with argparse subcommands generated from the definitions
- **`canasta.yml`** -- Ansible dispatcher playbook that validates and routes to command playbooks
- **`roles/`** -- 15 Ansible roles: common, orchestrator, create, delete, instance_lifecycle, config, extensions_skins, maintenance, mediawiki, backup, gitops, devmode, sitemap, upgrade, imagebuild
- **`roles/common/library/`** -- 5 custom Python modules for conf.json, .env, wikis.yaml, settings.yaml, and validation

### Orchestrator dispatch

All tasks in `roles/orchestrator/tasks/` dispatch to Docker Compose or Kubernetes based on the instance's orchestrator setting:

```bash
# Docker Compose (default)
./canasta create --id mysite --wiki main

# Kubernetes with Helm
./canasta create --id mysite --wiki main --domain-name example.com \
  --orchestrator kubernetes
```

The Kubernetes path uses a Helm chart (`roles/orchestrator/files/helm/canasta/`) for all workload management. The chart is self-contained — ConfigMaps for MediaWiki config, Caddy, Varnish, and MariaDB are rendered from `configData` in `values.yaml`. This means the Helm chart can be driven by Ansible (`helm upgrade`), Argo CD, or any tool that supports Helm values.

For the Argo CD GitOps path, `canasta gitops init` copies the chart and config data into a git repository. Config changes are made locally, then `canasta gitops push` syncs the config into `values.yaml` and pushes to the remote. Argo CD detects the change, re-renders the Helm templates, and updates the ConfigMaps in the cluster. Pods automatically restart when config changes (via checksum annotations).

Secrets (database passwords, MediaWiki secret key) are managed in K8s Secrets by Ansible and injected into the container environment at runtime.

### Registry

The instance registry (`conf.json`) lives on the controller node. All registry operations use `delegate_to: localhost` so they run on the controller regardless of which target host is being managed.

Existing Canasta-CLI installations are automatically visible -- the registry format is identical.

## Testing

```bash
# Unit tests (247 tests)
make test-unit

# Integration tests (requires Docker)
make test-integration

# Lint
make lint

# Validate command definitions
make validate

# Generate documentation
make docs
```

## Documentation

Generate command reference:

```bash
# Markdown files
make docs

# Wikitext for MediaWiki (dry run)
python scripts/wiki_publish.py --dry-run

# Publish to wiki
python scripts/wiki_publish.py \
  --api https://test-docs.canasta.wiki/w/api.php \
  --user User@BotName --pass botpassword
```

## Compatibility with Canasta-CLI

Canasta-Ansible is a drop-in replacement for Canasta-CLI:

- **Same command syntax**: `canasta create -i mysite -w main` works identically
- **Same registry format**: reads/writes the same `conf.json`
- **Same instance directory structure**: `.env`, `config/`, `docker-compose.yml`, etc.
- **Same Docker containers**: manages the same Canasta images and services
- **Zero migration**: install Canasta-Ansible, run `./canasta list`, see your existing instances

Additional capabilities not in Canasta-CLI:
- `--host` flag for remote host targeting
- `canasta doctor` for dependency checking
- `canasta storage setup nfs|efs` for Kubernetes storage provisioning
- Automatic Let's Encrypt TLS for Kubernetes instances
- Argo CD GitOps integration with self-healing ConfigMaps
- Auto-pull of latest playbooks during `canasta upgrade`
