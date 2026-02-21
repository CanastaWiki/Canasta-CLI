# Installation

This guide covers installing and uninstalling the Canasta command line interface (CLI).

## Contents

- [Prerequisites](#prerequisites)
- [Install](#install)
- [Verify installation](#verify-installation)
- [Updating](#updating)
- [Uninstall](#uninstall)
- [Post-installation notes](#post-installation-notes)

---

## Prerequisites

### Docker Compose (default)

Before using the Canasta CLI, you must have both Docker Engine and Docker Compose installed.

### Windows and macOS

Docker Compose is included in [Docker Desktop](https://www.docker.com/products/docker-desktop) for Windows and macOS.

### Linux

Linux is the most-tested and preferred OS environment as the host for Canasta. Installing the requirements is fast and easy to do on common Linux distributions such as Debian, Ubuntu, Red Hat, and CentOS. While you can get up and running with all the Docker requirements by installing Docker Desktop on Linux, if you are using a 'server environment' (no GUI), the recommended way to install is to **uninstall** any distribution-specific software and [install Docker software using the Docker repositories](https://docs.docker.com/compose/install/linux/#install-using-the-repository). (The link is the install guide for Docker Compose which will also install the Docker Engine.)

Essentially, preparing your Linux server to be a Canasta host by installing the Docker suite of software includes something like
`sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` once you've
added the Docker repositories to your system.

On Linux, you also need Docker access and file permission for your user account. Add your user to the `docker` and `www-data` groups, then log out and log back in:

```bash
sudo usermod -aG docker,www-data $USER
```

### Kubernetes (managed cluster)

To use Canasta with a CLI-managed Kubernetes cluster (`canasta create k8s --create-cluster`), you also need:

- **[kubectl](https://kubernetes.io/docs/tasks/tools/)** — the Kubernetes command-line tool
- **[kind](https://kind.sigs.k8s.io/)** — runs Kubernetes clusters in Docker containers

On macOS: `brew install kubectl kind`

On Linux: download the binaries from the links above or use your package manager.

Docker is still required since kind uses it to run cluster nodes. See the [Kubernetes guide](guide/kubernetes.md) for full details.

## Install

Run the automated installer (Linux/macOS):

```bash
curl -fsSL https://raw.githubusercontent.com/CanastaWiki/Canasta-CLI/main/install.sh | sudo bash
```

On Windows, use [WSL (Windows Subsystem for Linux)](https://docs.microsoft.com/en-us/windows/wsl/install) and run the installer inside your WSL distribution.

## Verify Installation

```bash
canasta version
```

## Updating

The CLI automatically updates itself when you run `canasta upgrade`. This ensures you always have the latest CLI version when upgrading your Canasta instances.

## Uninstall

First, delete any Canasta installations using `canasta delete` for each one.

Then remove the CLI binary and its configuration directory:

```bash
sudo rm /usr/local/bin/canasta
```

The configuration directory location depends on your platform:

- **Linux (root)**: `sudo rm -r /etc/canasta`
- **Linux (non-root)**: `rm -r ~/.config/canasta`
- **macOS**: `rm -r ~/Library/Application\ Support/canasta`

## Post-installation notes

### Email configuration

Email functionality is **not enabled by default**. To enable email for your wiki, you must configure the `$wgSMTP` setting in your wiki's settings file. See the [MediaWiki SMTP documentation](https://www.mediawiki.org/wiki/Manual:$wgSMTP) for configuration options.
