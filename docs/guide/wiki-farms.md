# Wiki farms

## What is a wiki farm?

A wiki farm is a single Canasta installation that hosts multiple wikis. All wikis in a farm share the same MediaWiki software, Docker containers, and Caddy reverse proxy, but each wiki has its own:

- **Database** — a separate MySQL database named after the wiki ID
- **Image directory** — uploaded files are stored per-wiki
- **Settings** — each wiki can have its own PHP settings files and can enable different extensions and skins
- **Admin account** — each wiki gets its own admin user and password

This makes wiki farms useful when you want to run several related wikis without the overhead of separate Docker stacks for each one.

Even a single-wiki Canasta installation uses the same underlying farm architecture — it is simply a farm with one wiki.

---

## How URLs work

Each wiki in a farm is identified by its URL, which determines how users reach it. The URL is set when you create or add a wiki and is stored in `config/wikis.yaml`.

### Path-based wikis

Multiple wikis share the same domain, distinguished by URL path. The first wiki in the farm is created with `canasta create` and gets the root path. Additional wikis are added with `canasta add` using a `domain/path` URL:

```bash
# Create the farm with the first wiki at the root
sudo canasta create -i myfarm -w mainwiki -n example.com -a admin

# Add a second wiki at example.com/docs
sudo canasta add -i myfarm -w docs -u example.com/docs -a admin

# Add a third wiki at example.com/internal
sudo canasta add -i myfarm -w internal -u example.com/internal -a admin
```

Users access these at `https://example.com`, `https://example.com/docs`, and `https://example.com/internal`.

### Subdomain-based wikis

Each wiki uses a different subdomain. This requires DNS records pointing each subdomain to your Canasta server. Caddy handles SSL/HTTPS automatically for all configured domains.

```bash
sudo canasta create -i myfarm -w mainwiki -n wiki.example.com -a admin
sudo canasta add -i myfarm -w docs -u docs.example.com -a admin
sudo canasta add -i myfarm -w community -u community.example.com -a admin
```

### Mixed

You can combine both approaches:

```bash
sudo canasta create -i myfarm -w mainwiki -n example.com -a admin
sudo canasta add -i myfarm -w docs -u example.com/docs -a admin
sudo canasta add -i myfarm -w community -u community.example.com -a admin
```

---

## Managing a wiki farm

### Viewing wikis

```bash
sudo canasta list
```

Example output:

```
Canasta ID  Wiki ID  Server Name  Server Path  Installation Path       Orchestrator
myfarm      mainwiki example.com  /            /home/user/myfarm       compose
myfarm      docs     example.com  /docs        /home/user/myfarm       compose
myfarm      community community.example.com / /home/user/myfarm       compose
```

### Per-wiki extension and skin management

Use the `-w` flag to target a specific wiki:

```bash
sudo canasta extension enable SemanticMediaWiki -i myfarm -w docs
sudo canasta skin enable CologneBlue -i myfarm -w community
```

Without `-w`, the command applies to all wikis in the farm.

### Removing a wiki

```bash
sudo canasta remove -i myfarm -w community
```

This deletes the wiki's database and configuration. You will be prompted for confirmation.

### Deleting the entire farm

```bash
sudo canasta delete -i myfarm
```

This stops and removes all containers, volumes, and configuration files.

See the [CLI Reference](../cli/canasta.md) for the full list of commands and flags.

---

## Installation directory structure

After creating a Canasta installation, the directory contains:

```
{installation-path}/
├── .env                           # Environment variables (domain, DB passwords, secret key)
├── docker-compose.yml             # Docker Compose configuration
├── docker-compose.override.yml    # Optional custom overrides
├── config/
│   ├── wikis.yaml                 # Wiki farm definition (IDs, URLs, display names)
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

This file defines all wikis in the farm:

```yaml
wikis:
- id: mainwiki
  url: example.com
  name: Main Wiki
- id: docs
  url: example.com/docs
  name: Documentation
- id: community
  url: community.example.com
  name: Community Wiki
```

The `url` field uses `domain/path` format without the protocol. The Caddyfile is regenerated from this file whenever wikis are added or removed.

### Settings

Settings files are loaded in alphabetical order:

- **Global settings** (`config/settings/global/*.php`) — loaded for every wiki in the farm
- **Per-wiki settings** (`config/settings/wikis/{wiki-id}/*.php`) — loaded only for that wiki

This lets you configure shared behavior globally while customizing individual wikis.

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
    "myfarm": {
      "Id": "myfarm",
      "Path": "/home/user/myfarm",
      "Orchestrator": "compose"
    }
  }
}
```

Most commands accept the `-i, --id` flag to identify an installation by name. If you run commands from within the installation directory, the `-i` flag is not required.

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
