# Wiki farms

A wiki farm allows you to run multiple wikis within the same Canasta installation. All wikis in a farm share the same MediaWiki software, but each wiki has its own database and image directory and can enable different extensions and skins.

With the Canasta CLI, you can:

- **Create** a new Canasta installation with an initial wiki using the `canasta create` command
- **Add** additional wikis to an existing installation using the `canasta add` command
- **Export** a wiki database using the `canasta export` command
- **Import** a database into an existing wiki using the `canasta import` command
- **Remove** wikis from an installation using the `canasta remove` command
- **List** all installations and their wikis using the `canasta list` command
- **Manage extensions and skins** for specific wikis using the `-w` flag with the `canasta extension` and `canasta skin` commands
- **Delete** a Canasta installation and all wikis that it hosts using the `canasta delete` command

See the [CLI Reference](canasta.md) for detailed flag and usage information for each command.

## URL schemes

Wikis in a farm can be accessed using different URL schemes:

- **Path-based**: Multiple wikis share the same domain with different paths (e.g., `example.com`, `example.com/wiki2`, `example.com/docs`)
- **Subdomain-based**: Each wiki uses a different subdomain (e.g., `wiki.example.com`, `docs.example.com`)
- **Mixed**: A combination of paths and subdomains

Subdomain-based routing requires that the subdomains are configured correctly in DNS to point to your Canasta server. Caddy handles SSL/HTTPS automatically for all configured domains.

---

## Global flags

```
Flags:
  -h, --help                 Help for canasta
  -v, --verbose              Verbose output
```

Most commands accept the `-i, --id` flag to identify a Canasta installation by the name you gave when creating it. If you run commands from within the Canasta installation directory, the `-i` flag is not required.

## Getting help

Running `canasta` with no arguments displays the full command listing and the location of the config file. For help with a specific command, use:

```bash
canasta [command] --help
```

For example:
```bash
canasta create --help
```

---

## Configuration files

Canasta installations have this structure:
```
{installation-path}/
  .env                           # Environment variables
  docker-compose.yml             # Docker Compose configuration
  docker-compose.override.yml    # Optional custom overrides
  config/
    wikis.yaml                   # Wiki farm configuration
    Caddyfile                    # Generated reverse proxy config
    SettingsTemplate.php         # Template for wiki settings
    admin-password_{wiki-id}     # Generated admin password per wiki
    {wiki-id}/
      Settings.php               # Wiki-specific settings
      LocalSettings.php          # Generated MediaWiki settings
```

## conf.json

The CLI maintains a registry of installations in a `conf.json` file. The location depends on the operating system and whether running as root:

- **Linux (root)**: `/etc/canasta/conf.json`
- **Linux (non-root)**: `~/.config/canasta/conf.json`
- **macOS**: `~/Library/Application Support/canasta/conf.json`

Example structure:
```json
{
  "Orchestrators": {},
  "Installations": {
    "wiki1": {
      "Id": "wiki1",
      "Path": "/home/user/wiki1",
      "Orchestrator": "compose"
    },
    "wiki2": {
      "Id": "wiki2",
      "Path": "/home/user/canasta/wiki2",
      "Orchestrator": "compose"
    }
  }
}
```

---

## Wiki farm example

This example demonstrates creating a wiki farm with multiple wikis using different URL schemes.

**1. Create the initial installation with the first wiki:**
```bash
sudo canasta create -i myfarm -w mainwiki -n example.com -a admin
```

**2. Add a wiki using a path on the same domain:**
```bash
sudo canasta add -i myfarm -w docs -u example.com/docs -t "Documentation Wiki" -a admin
```

**3. Add a wiki using a subdomain:**
```bash
sudo canasta add -i myfarm -w community -u community.example.com -a admin
```

**4. View all wikis in the farm:**
```bash
sudo canasta list
```

**5. Manage extensions for a specific wiki:**
```bash
sudo canasta extension enable SemanticMediaWiki -i myfarm -w docs
```

**6. Remove a wiki from the farm:**
```bash
sudo canasta remove -i myfarm -w community
```

---

## Running on non-standard ports

By default, Canasta uses ports 80 (HTTP) and 443 (HTTPS). If you need to run on different ports (e.g., to run multiple Canasta installations on the same server), you must pass an env file with the port settings using the `-e` flag and include the port in the domain name with `-n`.

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

### Example: Two wiki farms on the same server

To run two separate Canasta installations on the same server, the second installation must use different ports.

**1. Create the first wiki farm (uses default ports 80/443):**
```bash
sudo canasta create -i production -w mainwiki -n localhost -a admin
sudo canasta add -i production -w docs -u localhost/docs -a admin
```

**2. Create an .env file for the second farm with non-standard ports:**

Create a file called `staging.env`:
```env
HTTP_PORT=8080
HTTPS_PORT=8443
```

**3. Create the second wiki farm using the custom .env file and port in domain name:**
```bash
sudo canasta create -i staging -w testwiki -n localhost:8443 -a admin -e staging.env
```

Now you can access:
- First farm: `https://localhost` and `https://localhost/docs`
- Second farm: `https://localhost:8443`
