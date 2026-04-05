# Canasta-Ansible

Ansible-based management tool for [Canasta](https://canasta.wiki) MediaWiki installations. Full feature parity with [Canasta-CLI](https://github.com/CanastaWiki/Canasta-CLI) (Go), plus multi-host management.

## Features

- **57 commands** covering instance lifecycle, wiki management, configuration, extensions, skins, maintenance, backup/restore, gitops, devmode, sitemaps, storage provisioning, and more
- **Docker Compose and Kubernetes** (Helm + Argo CD) orchestrator support
- **Multi-host management** from a single controller node via SSH
- **Multi-node Kubernetes** with ConfigMap-based config, PVC storage, and CronJob backups
- **Auto-generated documentation** from a single command definitions file
- **247 unit tests** + Docker and Kubernetes integration tests in CI
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
sudo curl -o /usr/local/bin/canasta \
  https://raw.githubusercontent.com/CanastaWiki/Canasta-Ansible/main/canasta-docker
sudo chmod +x /usr/local/bin/canasta
```

The wrapper automatically pulls the `canasta-ansible` image on first
run and whenever `canasta upgrade` is invoked.

### Option 2: Native install

```bash
sudo git clone https://github.com/CanastaWiki/Canasta-Ansible.git /opt/canasta-ansible
cd /opt/canasta-ansible
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo .venv/bin/ansible-galaxy collection install -r requirements.yml
sudo ln -sf /opt/canasta-ansible/canasta-native /usr/local/bin/canasta
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
canasta --host ubuntu@prod1.example.com create --id wiki-prod --wiki main
```

This works without any inventory setup. SSH keys must be in place for
the target user on the target host.

For more control (custom Python interpreter, SSH port, etc.), create
a persistent hosts file at `$CANASTA_CONFIG_DIR/hosts.yml` (e.g.,
`~/.config/canasta/hosts.yml` or `~/Library/Application Support/canasta/hosts.yml`):

```yaml
all:
  hosts:
    prod1:
      ansible_host: prod1.example.com
      ansible_user: canasta
      ansible_python_interpreter: /usr/bin/python3
```

Then use the short name:

```bash
canasta --host prod1 create --id wiki-prod --wiki main
```

Manage instances across hosts:

```bash
# Create on a remote host
./canasta --host prod1.example.com create --id wiki-prod --wiki main

# Create on another host
./canasta --host staging.example.com create --id wiki-staging --wiki main

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

For multi-node clusters, set up a shared StorageClass (NFS, EFS, etc.).
The storage class is saved as the default for all future Kubernetes
instances:

```bash
./canasta storage setup nfs --server 10.0.0.1 --share /srv/nfs/canasta
./canasta create --id mysite --wiki main --domain-name example.com \
  --orchestrator kubernetes
```

To override the default, pass `--storage-class` explicitly.

For multi-node clusters, k3s scales by joining additional nodes
(`k3s agent --server <url> --token <token>`) — no instance migration
required. Pod placement across nodes is automatic by default and can
be controlled via `nodeSelector` and `affinity` rules in the instance's
`values.yaml`.

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
