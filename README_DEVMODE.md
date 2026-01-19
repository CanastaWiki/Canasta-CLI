# Canasta Development Mode

Development mode enables live code editing and step debugging with Xdebug for Canasta MediaWiki installations.

## Features

- **Live Code Editing**: MediaWiki code is extracted to a local directory and mounted into the container. Changes appear immediately without rebuilding.
- **Xdebug Integration**: Step through PHP code, set breakpoints, and inspect variables.
- **IDE Support**: Pre-configured for both VSCode and PHPStorm.

## Creating a Dev Mode Installation

```bash
# Use the default (latest) Canasta image
canasta create -i mydev -w mywiki -a admin --dev

# Or specify a specific Canasta image tag
canasta create -i mydev -w mywiki -a admin --dev dev-branch
```

The `--dev` flag accepts an optional image tag:
- `--dev` or `-D` — Uses the `latest` image (default)
- `--dev dev-branch` or `-D dev-branch` — Uses the specified image tag

This will:
1. Clone the Canasta stack
2. Extract MediaWiki code to `mediawiki-code/` for live editing
3. Build an Xdebug-enabled Docker image using the specified tag
4. Create IDE configuration files
5. Start the installation

## Using a Different Canasta Image

### At creation time (recommended)

Specify the image tag directly with the `--dev` flag:

```bash
canasta create -i mydev -w mywiki -a admin --dev dev-branch
```

### After creation

Edit the `.env` file in your installation directory:

```env
CANASTA_IMAGE_TAG=dev-branch
```

Then rebuild the xdebug image:

```bash
cd /path/to/installation
docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.dev.yml build
docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.dev.yml up -d
```

### Available Image Tags

- `latest` - Latest stable release (default)
- `dev` - Development branch
- Specific versions like `1.39`, `1.40`, etc.
- Any tag from [ghcr.io/canastawiki/canasta](https://github.com/CanastaWiki/Canasta/pkgs/container/canasta)

## IDE Setup

### VSCode

A `.vscode/launch.json` file is automatically created. To start debugging:

1. Open the installation directory in VSCode
2. Install the **PHP Debug** extension (by Xdebug)
3. Go to **Run and Debug** (Ctrl+Shift+D / Cmd+Shift+D)
4. Select **"Listen for Xdebug"** and click the play button
5. Set breakpoints in files under `mediawiki-code/`
6. Access your wiki in the browser - VSCode will break at your breakpoints

### PHPStorm

Configuration files are automatically created in the `.idea/` directory. To start debugging:

1. Open the installation directory in PHPStorm
2. Go to **Run** → **Edit Configurations**
3. The **"Listen for Xdebug"** configuration should already be available
4. Click the **phone/listen icon** in the toolbar (or **Run** → **Start Listening for PHP Debug Connections**)
5. Set breakpoints in files under `mediawiki-code/`
6. Access your wiki in the browser - PHPStorm will break at your breakpoints

#### Manual Configuration (if needed)

If the auto-created configuration doesn't work, verify these settings:

**Server Configuration** (Settings → PHP → Servers):
- **Name**: `canasta-dev`
- **Host**: `localhost`
- **Port**: `80` (important: use 80, not your external HTTPS port)
- **Debugger**: `Xdebug`
- **Use path mappings**: Checked
- **Path mapping**: `mediawiki-code` → `/var/www/mediawiki`

**Debug Settings** (Settings → PHP → Debug):
- **Debug port**: `9003`
- **Can accept external connections**: Checked

**Note**: Use port 80 in the server configuration because Xdebug reports the container's internal port, not the external Docker-mapped port.

## Bypassing Varnish Cache

Varnish caches page responses, so cached pages won't trigger breakpoints. To ensure PHP executes:

### Option 1: Add a cache-busting parameter

```
https://localhost/wiki/Main_Page?debug=1
```

Any unique query parameter forces a cache miss.

### Option 2: Log in to the wiki

Authenticated users bypass the Varnish cache automatically.

### Option 3: Purge specific pages

```bash
docker exec <installation>-web-1 curl -X PURGE http://varnish/wiki/Main_Page
```

## Running on Non-Standard Ports

If you need to run on non-standard ports (e.g., to avoid conflicts with another installation), edit `.env`:

```env
HTTP_PORT=8080
HTTPS_PORT=8443
MW_SITE_SERVER=https://localhost:8443
```

**Important**: If using wiki farm mode (`config/wikis.yaml`), you must also include the port in the wiki URL:

```yaml
wikis:
- id: wiki1
  url: localhost:8443
  name: wiki1
```

This is required because the wiki farm configuration uses the URL to match incoming requests and construct `$wgServer`.

> **Note**: Non-standard port support requires these upstream changes:
> - [CanastaWiki/Canasta-DockerCompose#72](https://github.com/CanastaWiki/Canasta-DockerCompose/pull/72) - Makes HTTP_PORT configurable
> - [CanastaWiki/CanastaBase#52](https://github.com/CanastaWiki/CanastaBase/pull/52) - Includes port in `$wgServer` for wiki farms

## Directory Structure

After creating a dev mode installation:

```
installation/
├── .env                      # Environment variables (includes CANASTA_IMAGE_TAG)
├── .vscode/
│   └── launch.json           # VSCode debug configuration
├── .idea/
│   ├── php.xml               # PHPStorm server configuration
│   └── runConfigurations/
│       └── Listen_for_Xdebug.xml  # PHPStorm run configuration
├── config/
│   ├── LocalSettings.php     # User's LocalSettings.php (loaded by Canasta)
│   ├── wikis.yaml            # Wiki farm configuration
│   ├── xdebug.ini            # Xdebug configuration
│   ├── Caddyfile             # Reverse proxy configuration
│   └── <wikiid>/             # Per-wiki settings directory
│       └── Settings.php
├── docker-compose.yml
├── docker-compose.override.yml
├── docker-compose.dev.yml    # Dev mode compose overlay
├── Dockerfile.xdebug         # Builds xdebug-enabled image
└── mediawiki-code/           # Extracted MediaWiki code (mounted into container)
    └── w/
        ├── index.php
        ├── extensions/
        ├── skins/
        └── ...
```

## Xdebug Configuration

The default Xdebug configuration (`config/xdebug.ini`):

```ini
xdebug.mode=debug
xdebug.start_with_request=yes
xdebug.client_host=host.docker.internal
xdebug.client_port=9003
xdebug.log=/var/log/mediawiki/php-xdebug.log
```

- **mode=debug**: Enables step debugging
- **start_with_request=yes**: Automatically connects to IDE on every request
- **client_host=host.docker.internal**: Docker's hostname for the host machine
- **client_port=9003**: Standard Xdebug 3 port

## Troubleshooting

### Breakpoints not hitting

1. **Check IDE is listening**: Ensure the debug listener is active
2. **Check path mappings**: The most common issue. Verify local path maps to `/var/www/mediawiki`
3. **Check Varnish cache**: Use a cache-busting parameter or log in
4. **Check Xdebug log**:
   ```bash
   docker exec <installation>-web-1 cat /var/log/mediawiki/php-xdebug.log
   ```

### PHPStorm detaches immediately

If you see "Cannot bind file" errors, your path mappings are incorrect. Ensure:
- Local path: `/path/to/installation/mediawiki-code`
- Remote path: `/var/www/mediawiki`

### Container startup hangs

The Xdebug configuration only applies to PHP-FPM (web requests), not CLI scripts. If you modified the configuration to also mount to CLI (`/etc/php/8.1/cli/conf.d/`), startup scripts may hang waiting for a debugger connection.

## Starting and Stopping

Dev mode installations use `canasta start` and `canasta stop` as normal:

```bash
canasta start -i mydev
canasta stop -i mydev
```

The CLI automatically detects dev mode and uses the appropriate compose files.
