# Canasta CLI

Welcome to the official documentation for **Canasta CLI**, the command-line interface tool for managing [Canasta](https://canasta.wiki/) MediaWiki installations.

## What is Canasta CLI?

Canasta CLI is a powerful tool that allows you to create, manage, and maintain multiple Canasta MediaWiki installations with ease. It supports Docker Compose orchestration and provides features for:

- **Creating** new Canasta installations (single wikis or wiki farms)
- **Managing** extensions and skins
- **Backing up** and restoring with Restic
- **Importing** and exporting wiki data
- **Upgrading** existing installations

## Supported Platforms

| Platform | Architectures |
|----------|--------------|
| Linux | AMD64/x86-64, ARM64/AArch64 |
| macOS | Intel (AMD64), Apple Silicon (ARM64) |
| Windows | Use [WSL](https://docs.microsoft.com/en-us/windows/wsl/install) with Linux version |

## Quick Start

```bash
# Install Canasta CLI
curl -fsSL https://raw.githubusercontent.com/CanastaWiki/Canasta-CLI/main/install.sh | sudo bash

# Create a new Canasta installation
canasta create -i myinstance -w wiki1 -a admin -n localhost
```

## Next Steps

- [Installation Guide](installation.md) - Detailed installation instructions
- [CLI Reference](cli/canasta.md) - Complete command documentation

### Guides

- [General Concepts](guide/general-concepts.md) - Installation IDs, wiki IDs, directory structure, settings, and configuration
- [Wiki Farms](guide/wiki-farms.md) - Running multiple wikis in one installation
- [Extensions and Skins](guide/extensions-and-skins.md) - Enabling, disabling, and adding extensions and skins
- [Development Mode](guide/devmode.md) - Live code editing and Xdebug debugging
- [Backup and Restore](guide/backup.md) - Setting up backups with restic
- [Best Practices](guide/best-practices.md) - Security considerations and best practices
- [Troubleshooting](guide/troubleshooting.md) - Common issues and debugging
