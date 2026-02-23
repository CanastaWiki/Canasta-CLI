# Best practices and security

This page covers security considerations and best practices for managing Canasta installations.

## Contents

- [Security considerations](#security-considerations)
  - [Password storage](#password-storage)
  - [Docker access](#docker-access)
  - [Network exposure](#network-exposure)
- [Operations](#operations)
  - [Backups](#backups)
  - [Before upgrading](#before-upgrading)
  - [Managing multiple installations](#managing-multiple-installations)
  - [Wiki ID naming rules](#wiki-id-naming-rules)

---

## Security considerations

### Password storage

- **Admin passwords** are stored in plaintext files at `config/admin-password_{wikiid}`
- **Database passwords** are stored in plaintext in the `.env` file
- Ensure proper file permissions on the installation directory to restrict access to these files
- Consider using environment variables when passing passwords on the command line to avoid exposing them in shell history:
  ```bash
  canasta create -i myinstance -w main -a admin --rootdbpass "$ROOT_DB_PASS"
  ```

### Docker access

Running Canasta CLI commands does **not** require `sudo`. However, your user account must have permission to run Docker commands:

- **macOS** — Docker Desktop grants Docker access to the current user automatically. No additional setup is needed.
- **Linux** — Add your user to the `docker` and `www-data` groups, then log out and log back in:
  ```bash
  sudo usermod -aG docker,www-data $USER
  ```
  The `docker` group grants permission to run Docker commands. The `www-data` group grants permission to manage files in the installation's `config/` directory, which is shared with the web container.
- **Windows (WSL)** — Follow the Linux instructions within your WSL distribution.

The only step that requires `sudo` is installing the CLI binary to `/usr/local/bin/` (see [Installation](../installation.md)).

### Network exposure

- By default, Canasta exposes ports for HTTP/HTTPS traffic
- Caddy handles SSL/TLS termination automatically
- To customize Caddy behavior (headers, redirects, etc.), edit `config/Caddyfile.site` — do not edit `config/Caddyfile` directly as it is regenerated when wikis change
- Review your `docker-compose.override.yml` if you need to customize port bindings or network settings

---

## Operations

### Backups

- Set up regular backups using `canasta backup` before making significant changes
- Always take a backup before running `canasta upgrade`
- Store backup passwords securely and separately from your server
- Test your backup restoration process periodically

### Before upgrading

See the [Upgrading guide](upgrading.md) for pre-upgrade steps and the full upgrade process.

### Managing multiple installations

- Use descriptive instance IDs that indicate the purpose (e.g., `company-wiki`, `docs-internal`)
- Keep a record of which wikis are in each installation if using wiki farms
- Use `canasta list` regularly to review your installations

### Wiki ID naming rules

Wiki IDs may contain only alphanumeric characters and underscores. Hyphens (`-`) are **not** allowed (they are allowed in installation IDs, but not wiki IDs). The following names are reserved and cannot be used: `settings`, `images`, `w`, `wiki`.

Valid examples: `mywiki`, `wiki_1`, `MyWiki2024`, `docs`

Invalid examples: `my-wiki`, `my wiki`, `wiki!`, `settings`

**Note:** Installation IDs (the `-i` flag) have different rules — they allow hyphens and underscores, must start and end with an alphanumeric character, and have no reserved names.
