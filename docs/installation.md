# Installation

This guide covers how to install Canasta CLI on your system.

## Prerequisites

- **Docker** and **Docker Compose** must be installed and running
- **Root/sudo access** for most operations

## Quick Install (Linux/macOS)

Run the automated installer:

```bash
curl -fsSL https://raw.githubusercontent.com/CanastaWiki/Canasta-CLI/main/install.sh | sudo bash
```

## Manual Installation

### Linux (AMD64)

```bash
curl -L https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta-linux-amd64 -o canasta
chmod +x canasta
sudo mv canasta /usr/local/bin/
```

### Linux (ARM64)

```bash
curl -L https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta-linux-arm64 -o canasta
chmod +x canasta
sudo mv canasta /usr/local/bin/
```

### macOS (Intel)

```bash
curl -L https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta-darwin-amd64 -o canasta
chmod +x canasta
sudo mv canasta /usr/local/bin/
```

### macOS (Apple Silicon)

```bash
curl -L https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta-darwin-arm64 -o canasta
chmod +x canasta
sudo mv canasta /usr/local/bin/
```

### Windows

Use [WSL (Windows Subsystem for Linux)](https://docs.microsoft.com/en-us/windows/wsl/install) and install the Linux version.

## Verify Installation

```bash
canasta version
```

## Updating

To update Canasta CLI, simply re-run the installation command or download the latest release.
