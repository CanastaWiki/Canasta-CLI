# General concepts

This page covers foundational concepts that apply to all Canasta installations, whether hosting a single wiki or a [wiki farm](wiki-farms.md).

## Contents

- [Installation IDs](#installation-ids)
- [Wiki IDs](#wiki-ids)
- [Installation directory structure](#installation-directory-structure)
- [Configuring logos](#configuring-logos)
- [Importing an existing wiki](#importing-an-existing-wiki)
- [Maintenance scripts](#maintenance-scripts)
  - [Automatic maintenance](#automatic-maintenance)
  - [Running the update sequence](#running-the-update-sequence)
  - [Running scripts manually](#running-scripts-manually)
- [Deploying behind a reverse proxy](#deploying-behind-a-reverse-proxy)
- [Running on non-standard ports](#running-on-non-standard-ports)

---

## Installation IDs

Every Canasta installation has an **installation ID** — a name you choose when creating it with the `-i` flag:

```bash
canasta create -i myinstance -w main -n localhost -a admin
```

The installation ID is used to refer to the installation in all subsequent commands:

```bash
canasta start -i myinstance
canasta extension list -i myinstance
canasta upgrade -i myinstance
```

If you run a command from within the installation directory, the `-i` flag is not required.

Installation IDs must start and end with an alphanumeric character and may contain letters, digits, hyphens (`-`), and underscores (`_`).

---

## Wiki IDs

Every wiki in a Canasta installation has a **wiki ID** — a short identifier set with the `-w` flag when creating or adding a wiki:

```bash
canasta create -i myinstance -w main -n localhost -a admin
```

Even a single-wiki installation requires a wiki ID. The wiki ID is used as:

- The MySQL database name for that wiki
- The directory name under `config/settings/wikis/`
- The suffix in the admin password file (`config/admin-password_{wikiid}`)
- The value passed to `-w` in commands like `canasta extension`, `canasta remove`, and `canasta export`

Wiki IDs may contain only alphanumeric characters and underscores. Hyphens are **not** allowed (unlike installation IDs). The names `settings`, `images`, `w`, and `wiki` are reserved. See [Wiki ID naming rules](best-practices.md#wiki-id-naming-rules) for details.

---

## Installation directory structure

After creating a Canasta installation, the directory contains:

```
{installation-path}/
├── .env                           # Environment variables (domain, DB passwords, secret key)
├── docker-compose.yml             # Docker Compose configuration
├── docker-compose.override.yml    # Optional custom overrides
├── config/
│   ├── wikis.yaml                 # Wiki definition (IDs, URLs, display names)
│   ├── Caddyfile                  # Generated reverse proxy config (do not edit)
│   ├── Caddyfile.custom           # User customizations for Caddy site block
│   ├── Caddyfile.global           # User customizations for Caddy global options
│   ├── admin-password_{wiki-id}   # Generated admin password per wiki
│   └── settings/
│       ├── global/                # PHP settings loaded for all wikis
│       │   └── *.php
│       └── wikis/
│           └── {wiki-id}/         # PHP settings loaded for a specific wiki
│               └── *.php
├── extensions/                    # User extensions
├── skins/                         # User skins
│   └── logos/                     # Public logos (optional, see Configuring logos)
└── images/                        # Uploaded files
```

### conf.json

The CLI maintains a registry of all installations in a `conf.json` file. The location depends on the platform:

- **Linux (root)**: `/etc/canasta/conf.json`
- **Linux (non-root)**: `~/.config/canasta/conf.json`
- **macOS**: `~/Library/Application Support/canasta/conf.json`

Example:

```json
{
  "Orchestrators": {},
  "Installations": {
    "myinstance": {
      "Id": "myinstance",
      "Path": "/home/user/myinstance",
      "Orchestrator": "compose"
    }
  }
}
```

### wikis.yaml

This file defines the wikis in the installation. Even a single-wiki installation has a `wikis.yaml`:

```yaml
wikis:
- id: main
  url: localhost
  name: My Wiki
```

For a multi-wiki farm, it lists all wikis:

```yaml
wikis:
- id: mainwiki
  url: example.com
  name: Main Wiki
- id: docs
  url: example.com/docs
  name: Documentation
```

The `url` field uses `domain/path` format without the protocol. The Caddyfile is regenerated from this file whenever wikis are added or removed.

### Caddyfile, Caddyfile.custom, and Caddyfile.global

Canasta uses [Caddy](https://caddyserver.com/) as its reverse proxy. The `config/Caddyfile` is auto-generated from `wikis.yaml` and is overwritten whenever wikis are added or removed — do not edit it directly.

To add custom Caddy directives for the site block (headers, rewrites, etc.), edit `config/Caddyfile.custom`. To add global Caddy options, edit `config/Caddyfile.global`. Both files are imported by the generated Caddyfile and are never overwritten.

Example `Caddyfile.custom`:

```
header X-Frame-Options "SAMEORIGIN"
header X-Content-Type-Options "nosniff"
```

See the [Caddy documentation](https://caddyserver.com/docs/caddyfile/directives) for available directives.

If deploying behind an external reverse proxy that handles SSL termination, see [Deploying behind a reverse proxy](#deploying-behind-a-reverse-proxy).

### Settings

Settings files are PHP files placed in the settings directories and loaded in lexicographic order:

- **Global settings** (`config/settings/global/*.php`) — loaded for every wiki
- **Per-wiki settings** (`config/settings/wikis/{wiki-id}/*.php`) — loaded only for that wiki

This lets you configure shared behavior globally while customizing individual wikis where needed.

### Passwords

- **Admin passwords** are saved to `config/admin-password_{wikiid}` — one file per wiki
- **Database passwords** are saved to the `.env` file (`MYSQL_PASSWORD` for root, `WIKI_DB_PASSWORD` for the wiki user)

If not specified at creation time, passwords are auto-generated (30 characters with digits and symbols).

---

## Configuring logos

MediaWiki uses the `$wgLogos` configuration variable to set the wiki logo. See the [MediaWiki documentation](https://www.mediawiki.org/wiki/Manual:$wgLogos) for details on logo sizes and formats.

There are two approaches to configuring logos in Canasta, depending on whether you want the logo to follow wiki permissions or be publicly accessible.

### Option 1: Upload via MediaWiki (recommended)

Upload your logo through MediaWiki's `Special:Upload` page, then reference it in a settings file:

```php
<?php
# config/settings/wikis/{wiki-id}/Logo.php

$wgLogos = [
    '1x' => $wgUploadPath . '/Logo.png',
];
```

This approach:

- **Follows wiki permissions** — on public wikis, anyone can see the logo; on private wikis, only logged-in users can see it
- **Works for wiki farms** — each wiki uploads its own logo
- **Persists across upgrades** — uploaded files are stored in the `images/` directory

Use this option for most wikis, including fully private wikis where the logo should not be visible to logged-out users.

### Option 2: Static file in skins directory

For private wikis that need a publicly accessible logo (for example, to show branding on the login page), place the logo in the `skins/logos/` directory:

```bash
mkdir -p skins/logos
cp /path/to/logo.png skins/logos/{wiki-id}.png
```

Then configure it in a per-wiki settings file:

```php
<?php
# config/settings/wikis/{wiki-id}/Logo.php

$wgLogos = [
    '1x' => "/w/user-skins/logos/{$wgDBname}.png",
];
```

This approach:

- **Bypasses wiki permissions** — the logo is always publicly accessible
- **Works for wiki farms** — each wiki has its own `{wiki-id}.png` file
- **Persists across upgrades** — the `skins/` directory is a mounted volume

Use this option only when you need a visible logo on private wikis.

### Summary

| Scenario | Solution |
|----------|----------|
| Public wiki | Upload via MediaWiki |
| Private wiki, logo hidden from logged-out users | Upload via MediaWiki |
| Private wiki, logo visible on login page | Static file in `skins/logos/` |

---

## Importing an existing wiki

To migrate an existing MediaWiki installation into Canasta, prepare a database dump (`.sql` or `.sql.gz` file) and pass it with the `-d` flag:

```bash
canasta create -i myinstance -w main -n localhost -d ./backup.sql.gz -a admin
```

You can also provide a per-wiki settings file and an environment file with password overrides:

```bash
canasta create -i myinstance -w main -n localhost -d ./backup.sql.gz -l ./my-settings.php -e ./custom.env -a admin
```

The `-l` flag (`--wiki-settings`) copies the specified file to `config/settings/wikis/{wiki-id}/`, preserving the filename. Use it to bring over custom settings from an existing wiki.

To import a database into an additional wiki in an existing installation, use `canasta add` with the `--database` flag:

```bash
canasta add -i myinstance -w docs -u example.com/docs -d ./docs-backup.sql.gz -a admin
```

See the [CLI Reference](../cli/canasta_create.md) for the full list of flags.

---

## Maintenance scripts

MediaWiki includes over 200 [maintenance scripts](https://www.mediawiki.org/wiki/Manual:Maintenance_scripts) for tasks like changing passwords, importing images, and managing the database.

### Automatic maintenance

You generally do not need to run maintenance scripts manually:

- **`update.php`** runs automatically during container startup. If you need to run it, simply restart the installation with `canasta restart`.
- **`runJobs.php`** runs automatically in the background for the entire life of the container.

### Running the update sequence

Use `canasta maintenance update` to run the standard update sequence: `update.php`, `runJobs.php`, and (if Semantic MediaWiki is installed) `rebuildData.php`. Each script runs separately with its output streamed in real time.

```bash
canasta maintenance update -i myinstance
```

In a wiki farm, use `--wiki` to target a specific wiki or `--all` to run on every wiki:

```bash
canasta maintenance update -i myinstance --wiki=docs
canasta maintenance update -i myinstance --all
```

If the installation has only one wiki, it is selected automatically. If there are multiple wikis and neither `--wiki` nor `--all` is specified, the command will error with guidance.

Use `--skip-jobs` and `--skip-smw` to skip individual steps:

```bash
# Run only update.php
canasta maintenance update -i myinstance --skip-jobs --skip-smw
```

### Running scripts manually

To run an arbitrary maintenance script, use `canasta maintenance script`. Wrap the script name and any arguments in quotes so they are passed as a single argument:

```bash
canasta maintenance script "createAndPromote.php WikiSysop MyPassword --bureaucrat --sysop" -i myinstance
```

Use `--wiki` to target a specific wiki in a farm:

```bash
canasta maintenance script "rebuildrecentchanges.php" -i myinstance --wiki=docs
```

See the [CLI Reference](../cli/canasta_maintenance.md) for more details.

---

## Deploying behind a reverse proxy

When running Canasta behind an external reverse proxy that terminates SSL and forwards requests to Canasta over HTTP (such as nginx, a cloud load balancer, or Cloudflare in "Flexible SSL" mode), you must disable Caddy's automatic HTTPS handling. Otherwise, Caddy may attempt to redirect requests or provision certificates, causing redirect loops or certificate errors.

To configure this, create an env file with the `CADDY_AUTO_HTTPS` setting:

```env
CADDY_AUTO_HTTPS=off
```

Pass this file when creating the installation:

```bash
canasta create -i myinstance -w main -n example.com -a admin -e custom.env
```

This generates a Caddyfile with:

- `auto_https off` in the global options block (disables automatic certificate provisioning)
- `header_up Host {host}` in the reverse proxy directive (preserves the original Host header through the proxy chain)

For an existing installation, add `CADDY_AUTO_HTTPS=off` to the `.env` file and run `canasta upgrade` to regenerate the Caddyfile.

---

## Running on non-standard ports

By default, Canasta uses ports 80 (HTTP) and 443 (HTTPS). To use different ports — for example, to run multiple Canasta installations on the same server — set port variables in the `.env` file and include the port in the domain name.

For a new installation, pass an env file with port settings:

Create a file called `custom.env`:
```env
HTTP_PORT=8080
HTTPS_PORT=8443
```

```bash
canasta create -i staging -w testwiki -n localhost:8443 -a admin -e custom.env
```

For an existing installation, edit `.env` to set the ports, update `config/wikis.yaml` to include the port in the URL, and restart:

```env
HTTP_PORT=8080
HTTPS_PORT=8443
```

```yaml
wikis:
- id: wiki1
  url: localhost:8443
  name: wiki1
```

```bash
canasta restart -i myinstance
```

### Example: two installations on the same server

```bash
# First installation uses default ports (80/443)
canasta create -i production -w mainwiki -n localhost -a admin

# Second installation uses custom ports
canasta create -i staging -w testwiki -n localhost:8443 -a admin -e custom.env
```

Access them at `https://localhost` and `https://localhost:8443`.
