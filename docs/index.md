# Canasta CLI

Welcome to the official documentation for **Canasta CLI**, the command-line interface tool for managing [Canasta](https://canasta.wiki/) MediaWiki installations.

## What is Canasta CLI?

Canasta CLI is a powerful tool that allows you to create, manage, and maintain multiple Canasta MediaWiki installations with ease. It supports Docker Compose and local Kubernetes orchestration and provides features for:

- **Creating** new Canasta installations (single wikis or wiki farms)
- **Managing** extensions and skins
- **Backing up** and restoring with Restic
- **Importing** and exporting wiki data
- **Upgrading** existing installations

## Supported platforms

| Platform | Architectures |
|----------|--------------|
| Linux | AMD64/x86-64, ARM64/AArch64 |
| macOS | Intel (AMD64), Apple Silicon (ARM64) |
| Windows | Use [WSL](https://docs.microsoft.com/en-us/windows/wsl/install) with Linux version |

## Quick start

```bash
# Install Canasta CLI
curl -fsSL https://raw.githubusercontent.com/CanastaWiki/Canasta-CLI/main/install.sh | sudo bash

# Create a new Canasta installation
canasta create -i myinstance -w wiki1 -a admin -n localhost
```

> **Note: Linux users**
>
> Before running CLI commands, add your user to the `docker` and `www-data` groups. See the [installation guide](installation.md#linux) for details.

## Next steps

- [Installation guide](installation.md) - Detailed installation instructions
- [CLI reference](cli/canasta.md) - Complete command documentation

### Guides

- [General concepts](guide/general-concepts.md) - Installation IDs, wiki IDs, directory structure, settings, and configuration
- [Wiki farms](guide/wiki-farms.md) - Running multiple wikis in one installation
- [Extensions and skins](guide/extensions-and-skins.md) - Enabling, disabling, and adding extensions and skins
- [Observability](guide/observability.md) - Log aggregation with OpenSearch and Dashboards
- [Development mode](guide/devmode.md) - Live code editing and Xdebug debugging
- [Kubernetes](guide/kubernetes.md) - Deploying to Kubernetes with a CLI-managed kind cluster
- [Backup and restore](guide/backup.md) - Setting up backups with restic
- [Upgrading](guide/upgrading.md) - Upgrade process, version notes, and legacy migration
- [Sitemaps](guide/sitemaps.md) - XML sitemap generation for search engine indexing
- [Best practices](guide/best-practices.md) - Security considerations and best practices
- [Troubleshooting](guide/troubleshooting.md) - Common issues and debugging
