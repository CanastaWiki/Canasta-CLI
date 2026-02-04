# General concepts

This page covers foundational concepts that apply to all Canasta installations, whether hosting a single wiki or a [wiki farm](wiki-farms.md).

---

## Installation IDs

Every Canasta installation has an **installation ID** — a name you choose when creating it with the `-i` flag:

```bash
sudo canasta create -i mywiki -w main -n localhost -a admin
```

The installation ID is used to refer to the installation in all subsequent commands:

```bash
sudo canasta start -i mywiki
sudo canasta extension list -i mywiki
sudo canasta upgrade -i mywiki
```

If you run a command from within the installation directory, the `-i` flag is not required.

Installation IDs must start and end with an alphanumeric character and may contain letters, digits, hyphens (`-`), and underscores (`_`).

---

## Wiki IDs

Every wiki in a Canasta installation has a **wiki ID** — a short identifier set with the `-w` flag when creating or adding a wiki:

```bash
sudo canasta create -i mywiki -w main -n localhost -a admin
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
│   ├── Caddyfile                  # Generated reverse proxy config
│   ├── admin-password_{wiki-id}   # Generated admin password per wiki
│   └── settings/
│       ├── global/                # PHP settings loaded for all wikis
│       │   └── *.php
│       └── wikis/
│           └── {wiki-id}/         # PHP settings loaded for a specific wiki
│               └── Settings.php
├── extensions/                    # User extensions
└── skins/                        # User skins
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

### Settings

Settings files are PHP files placed in the settings directories and loaded in alphabetical order:

- **Global settings** (`config/settings/global/*.php`) — loaded for every wiki
- **Per-wiki settings** (`config/settings/wikis/{wiki-id}/*.php`) — loaded only for that wiki

This lets you configure shared behavior globally while customizing individual wikis where needed.

### Passwords

- **Admin passwords** are saved to `config/admin-password_{wikiid}` — one file per wiki
- **Database passwords** are saved to the `.env` file (`MYSQL_PASSWORD` for root, `WIKI_DB_PASSWORD` for the wiki user)

If not specified at creation time, passwords are auto-generated (30 characters with digits and symbols).

---

## conf.json

The CLI maintains a registry of all installations in a `conf.json` file. The location depends on the platform:

- **Linux (root)**: `/etc/canasta/conf.json`
- **Linux (non-root)**: `~/.config/canasta/conf.json`
- **macOS**: `~/Library/Application Support/canasta/conf.json`

Example:

```json
{
  "Orchestrators": {},
  "Installations": {
    "mywiki": {
      "Id": "mywiki",
      "Path": "/home/user/mywiki",
      "Orchestrator": "compose"
    }
  }
}
```

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
sudo canasta create -i staging -w testwiki -n localhost:8443 -a admin -e custom.env
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
sudo canasta restart -i myinstance
```

### Example: two installations on the same server

```bash
# First installation uses default ports (80/443)
sudo canasta create -i production -w mainwiki -n localhost -a admin

# Second installation uses custom ports
sudo canasta create -i staging -w testwiki -n localhost:8443 -a admin -e custom.env
```

Access them at `https://localhost` and `https://localhost:8443`.
