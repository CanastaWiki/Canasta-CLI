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
curl -L https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta-linux-amd64 -o canasta
chmod +x canasta
sudo mv canasta /usr/local/bin/

# Create a new Canasta installation
sudo canasta create -i my-wiki -w wiki1 -a admin -n localhost
```

## Next Steps

- [Installation Guide](installation.md) - Detailed installation instructions
- [CLI Reference](cli/canasta.md) - Complete command documentation

### Guides

- [Wiki Farms](wiki-farms.md) - Wiki farm concepts, URL schemes, configuration files, and examples
- [Development Mode](devmode.md) - Live code editing and Xdebug debugging
- [Backup and Restore](backup.md) - Setting up backups with restic
- [Best Practices](best-practices.md) - Security considerations and best practices
- [Troubleshooting](troubleshooting.md) - Common issues and debugging
