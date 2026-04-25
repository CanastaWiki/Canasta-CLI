# Canasta CLI

Canasta CLI: command-line tool for managing [Canasta](https://canasta.wiki) MediaWiki installations. Supports Docker Compose and Kubernetes orchestrators with single-host and multi-host management.

> **Note:** This repository is the python/Ansible implementation of the Canasta CLI (version 4.0.0+). It supersedes the legacy Go implementation, which is archived at [CanastaWiki/Canasta-Go](https://github.com/CanastaWiki/Canasta-Go) (end-of-life as of v3.7.0). See the [Upgrading guide](https://canasta.wiki/wiki/Help:Upgrading) for the migration path.

## Documentation

User documentation lives at **[canasta.wiki](https://canasta.wiki)** — installation, quick start, command reference, multi-host management, GitOps, backup, and more.

To install: `curl -fsSL https://get.canasta.wiki | bash` (Docker mode, recommended) or add `-s -- --native` for native mode. See [Help:Installation](https://canasta.wiki/wiki/Help:Installation) for details.

## Issues

Report bugs and request features at **[GitHub Issues](https://github.com/CanastaWiki/Canasta-CLI/issues)**.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the local dev workflow (test commands, repo layout, regenerating docs).

For ecosystem-wide contributing guidance (CanastaBase, Canasta image, custom distributions), see the [Contributing guide on canasta.wiki](https://canasta.wiki/wiki/Help:Contributing).

## Release notes

See [RELEASE_NOTES.md](RELEASE_NOTES.md).
