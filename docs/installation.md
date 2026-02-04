# Installation

This guide covers installing and uninstalling the Canasta command line interface (CLI).

## Prerequisites

Before using the Canasta CLI, you must have both Docker Engine and Docker Compose installed.

### Windows and macOS

Docker Compose is included in [Docker Desktop](https://www.docker.com/products/docker-desktop) for Windows and macOS.

### Linux

Linux is the most-tested and preferred OS environment as the host for Canasta. Installing the requirements is fast and easy to do on common Linux distributions such as Debian, Ubuntu, Red Hat, and CentOS. While you can get up and running with all the Docker requirements by installing Docker Desktop on Linux, if you are using a 'server environment' (no GUI), the recommended way to install is to **uninstall** any distribution-specific software and [install Docker software using the Docker repositories](https://docs.docker.com/compose/install/linux/#install-using-the-repository). (The link is the install guide for Docker Compose which will also install the Docker Engine.)

Essentially, preparing your Linux server to be a Canasta host by installing the Docker suite of software includes something like
`sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` once you've
added the Docker repositories to your system.

On Linux, you also need Docker access for your user account. Add your user to the `docker` group, then log out and log back in:

```bash
sudo usermod -aG docker $USER
```

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

## Uninstall

To uninstall the Canasta CLI:

```bash
sudo rm /usr/local/bin/canasta
sudo rm -r /etc/canasta
```

**Note:** This only removes the CLI. To delete Canasta installations, use `canasta delete` for each installation first.

## Post-installation notes

### Email configuration

Email functionality is **not enabled by default**. To enable email for your wiki, you must configure the `$wgSMTP` setting in your wiki's settings file. See the [MediaWiki SMTP documentation](https://www.mediawiki.org/wiki/Manual:$wgSMTP) for configuration options.
