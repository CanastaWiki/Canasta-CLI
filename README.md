# Canasta-Ansible

Ansible-based management tool for [Canasta](https://canasta.wiki) MediaWiki installations. Full feature parity with [Canasta-CLI](https://github.com/CanastaWiki/Canasta-CLI) (Go), plus multi-host management, instance migration, and cloning.

## Features

- **56 commands** covering instance lifecycle, wiki management, configuration, extensions, skins, maintenance, backup/restore, gitops, devmode, sitemaps, and more
- **Docker Compose and Kubernetes** (kind) orchestrator support
- **Multi-host management** from a single controller node via SSH
- **Instance migration and cloning** between hosts with backup schedule transfer
- **Auto-generated documentation** from a single command definitions file
- **202 unit tests** (87% coverage) + Docker-based integration tests in CI
- **Zero-migration compatibility** with existing Canasta-CLI installations (reads the same `conf.json` registry)

## Requirements

### Controller node

| Requirement | Purpose |
|------------|---------|
| Python 3.9+ | Ansible runtime |
| ansible-core 2.15+ | Playbook execution |
| Git | Playbook auto-update during upgrade |

### Target host

| Requirement | Purpose |
|------------|---------|
| Python 3 | Ansible module execution |
| Docker + Docker Compose v2 | Container orchestration (Compose) |
| **or** kubectl + kind | Container orchestration (Kubernetes) |
| SSH server | Remote management (not needed if controller = target) |

Optional (depending on features used):

| Requirement | Purpose |
|------------|---------|
| git + git-crypt | GitOps commands |
| rsync | Migrate/clone commands |

Run `canasta doctor` to verify all dependencies on a target host.

## Installation

### Option 1: Docker (recommended)

Only Docker is required. No Python, no Ansible.

```bash
sudo curl -o /usr/local/bin/canasta \
  https://raw.githubusercontent.com/CanastaWiki/Canasta-Ansible/main/canasta-docker
sudo chmod +x /usr/local/bin/canasta
```

The wrapper automatically pulls the `canasta-ansible` image on first run.

Upgrade: `docker pull ghcr.io/canastawiki/canasta-ansible:latest`

### Option 2: Native install

```bash
sudo git clone https://github.com/CanastaWiki/Canasta-Ansible.git /opt/canasta-ansible
cd /opt/canasta-ansible
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
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

Add remote hosts to the inventory:

```bash
cp inventory/hosts.yml.example inventory/hosts.yml
# Edit inventory/hosts.yml to add your target hosts
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

# Clone production to staging
./canasta clone --id wiki-prod --from prod1.example.com \
  --to staging.example.com --new-id wiki-staging \
  --new-domain staging.example.com --yes
```

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
| `canasta gitops fix-submodules` | Fix submodule registration |

### Development

| Command | Description |
|---------|-------------|
| `canasta devmode enable\|disable` | Toggle development mode (xdebug) |

### Multi-host (Ansible-only)

| Command | Description |
|---------|-------------|
| `canasta migrate` | Move an instance between hosts |
| `canasta clone` | Clone an instance to another host |

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
- **`roles/`** -- 16 Ansible roles: common, orchestrator, create, delete, instance_lifecycle, config, extensions_skins, maintenance, mediawiki, backup, gitops, devmode, sitemap, transfer, upgrade, imagebuild
- **`roles/common/library/`** -- 5 custom Python modules for conf.json, .env, wikis.yaml, settings.yaml, and validation

### Orchestrator dispatch

All tasks in `roles/orchestrator/tasks/` dispatch to Docker Compose or Kubernetes based on the instance's orchestrator setting:

```bash
# Docker Compose (default)
./canasta create --id mysite --wiki main

# Kubernetes with kind
./canasta create --id mysite --wiki main --orchestrator kubernetes --create-cluster
```

### Registry

The instance registry (`conf.json`) lives on the controller node. All registry operations use `delegate_to: localhost` so they run on the controller regardless of which target host is being managed.

Existing Canasta-CLI installations are automatically visible -- the registry format is identical.

## Testing

```bash
# Unit tests (202 tests, 87% coverage)
make test-unit

# Integration tests (requires Docker)
cd tests/integration && molecule test -s lifecycle

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
- `canasta migrate` and `canasta clone` for instance transfer
- `canasta doctor` for dependency checking
- Auto-pull of latest playbooks during `canasta upgrade`
