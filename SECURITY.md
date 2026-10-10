# Security Policy

## Reporting a vulnerability

Please do not report security vulnerabilities through public issues, pull requests, or discussions.

Report them privately through GitHub: open this repository's **Security** tab and choose **Report a vulnerability**. This creates a private advisory visible only to you and the maintainers.

Include what you can of:
- The Canasta CLI version affected (`canasta version`)
- Whether the installation uses Docker Compose or Kubernetes
- Steps to reproduce, or a proof of concept
- The impact you expect

## Supported versions

Security fixes are made to the latest Canasta CLI release. Please confirm the issue on the latest release before reporting.

## Scope

This policy covers the Canasta CLI:
- The `canasta` command, its Ansible playbooks and roles, and the installer
- The Docker Compose and Kubernetes configuration it generates, including the Helm chart
- The handling of passwords, keys, and other secrets in installations and gitops repositories
- The `canasta-ansible` and `canasta-caddy` images built from this repository

Report these elsewhere:
- Vulnerabilities in MediaWiki core or Wikimedia-maintained extensions and skins: https://www.mediawiki.org/wiki/Reporting_security_bugs
- Vulnerabilities in the Canasta image or its bundled extensions and skins: https://github.com/CanastaWiki/Canasta
- Vulnerabilities in the CanastaBase image, its scripts, or its Apache and PHP configuration: https://github.com/CanastaWiki/CanastaBase
- Vulnerabilities in Ansible, Kubernetes, Docker, Caddy, or other upstream dependencies: the upstream project

If you are unsure where an issue belongs, report it here and we will help route it.
