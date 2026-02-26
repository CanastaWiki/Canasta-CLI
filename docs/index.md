# Canasta Documentation

[Canasta](https://canasta.wiki/) is a fully featured MediaWiki distribution that bundles MediaWiki with over 170 extensions and skins, a database server, a web server, and a caching layer into a single containerized package supporting both Docker Compose and Kubernetes. It is designed to make it easy to set up, manage, and maintain enterprise-grade wikis without needing deep knowledge of MediaWiki's internals.

## Canasta CLI

The **Canasta CLI** is the command-line tool for managing Canasta installations. It supports Docker Compose and local Kubernetes orchestration and provides features for:

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
canasta create -i myinstance -w wiki1 -n localhost
```

> **Note: Linux users**
>
> Before running CLI commands, add your user to the `docker` and `www-data` groups. See the [installation guide](installation.md#linux) for details.

## Next steps

- [Installation guide](installation.md) - Detailed installation instructions
- [CLI reference](cli/canasta.md) - Complete command documentation

### Guides

- [General concepts](guide/general-concepts.md) - Installation IDs, wiki IDs, directory structure, settings, and configuration
- [Best practices](guide/best-practices.md) - Security considerations and best practices
- [Extensions and skins](guide/extensions-and-skins.md) - Enabling, disabling, and adding extensions and skins
- [Wiki farms](guide/wiki-farms.md) - Running multiple wikis in one installation
- [Backup and restore](guide/backup.md) - Setting up backups with restic
- [Upgrading](guide/upgrading.md) - Upgrade process, version notes, and legacy migration
- [Orchestrators](guide/orchestrators.md) - Docker Compose and Kubernetes orchestrator details
- [Observability](guide/observability.md) - Log aggregation with OpenSearch and Dashboards
- [Sitemaps](guide/sitemaps.md) - XML sitemap generation for search engine indexing
- [Development mode](guide/devmode.md) - Live code editing and Xdebug debugging
- [Contributing](guide/contributing.md) - Release workflow and repository overview
- [Troubleshooting](guide/troubleshooting.md) - Common issues and debugging
