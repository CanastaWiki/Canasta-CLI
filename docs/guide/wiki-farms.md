# Wiki farms

## What is a wiki farm?

A wiki farm is a single Canasta installation that hosts multiple wikis. All wikis in a farm share the same MediaWiki software, Docker containers, Caddy reverse proxy, and can share global PHP settings, extensions, and skins. At the same time, each wiki has its own:

- **Database** — a separate MySQL database named after the wiki ID
- **Image directory** — uploaded files are stored per-wiki
- **Settings** — each wiki can have its own PHP settings files and can enable or disable extensions and skins independently
- **Admin account** — each wiki gets its own admin user and password

This makes wiki farms useful when you want to run several related wikis without the overhead of separate Docker stacks for each one, while still being able to configure each wiki individually where needed.

Even a single-wiki Canasta installation uses the same underlying architecture — it is simply a farm with one wiki. See [General concepts](general-concepts.md) for details on installation structure, wiki IDs, settings, and other topics that apply to all installations.

## Contents

- [How wiki farm URLs work](#how-wiki-farm-urls-work)
- [Managing a wiki farm](#managing-a-wiki-farm)

---

## How wiki farm URLs work

Each wiki in a farm is identified by its URL, which determines how users reach it. The URL is set when you create or add a wiki and is stored in `config/wikis.yaml`.

### Path-based wikis

Multiple wikis share the same domain, distinguished by URL path. The first wiki in the farm is created with `canasta create` and gets the root path. Additional wikis are added with `canasta add` using a `domain/path` URL:

```bash
# Create the farm with the first wiki at the root
canasta create -i myfarm -w mainwiki -n example.com -a admin

# Add a second wiki at example.com/docs
canasta add -i myfarm -w docs -u example.com/docs -a admin

# Add a third wiki at example.com/internal
canasta add -i myfarm -w internal -u example.com/internal -a admin
```

Users access these at `https://example.com`, `https://example.com/docs`, and `https://example.com/internal`.

### Subdomain-based wikis

Each wiki uses a different subdomain. This requires DNS records pointing each subdomain to your Canasta server. Caddy handles SSL/HTTPS automatically for all configured domains.

```bash
canasta create -i myfarm -w mainwiki -n wiki.example.com -a admin
canasta add -i myfarm -w docs -u docs.example.com -a admin
canasta add -i myfarm -w community -u community.example.com -a admin
```

### Mixed

You can combine both approaches:

```bash
canasta create -i myfarm -w mainwiki -n example.com -a admin
canasta add -i myfarm -w docs -u example.com/docs -a admin
canasta add -i myfarm -w community -u community.example.com -a admin
```

---

## Managing a wiki farm

### Viewing wikis

```bash
canasta list
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
canasta extension enable SemanticMediaWiki -i myfarm -w docs
canasta skin enable CologneBlue -i myfarm -w community
```

Without `-w`, the command applies to all wikis in the farm.

### Removing a wiki

```bash
canasta remove -i myfarm -w community
```

This deletes the wiki's database and configuration. You will be prompted for confirmation.

### Deleting the entire farm

```bash
canasta delete -i myfarm
```

This stops and removes all containers, volumes, and configuration files.

See the [CLI Reference](../cli/canasta.md) for the full list of commands and flags.
