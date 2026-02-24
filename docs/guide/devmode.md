# Development mode

Development mode enables live code editing and step debugging with Xdebug for Canasta MediaWiki installations.

## Contents

- [Features](#features)
- [Enabling dev mode](#enabling-dev-mode)
- [Building from local source](#building-from-local-source)
- [Disabling dev mode](#disabling-dev-mode)
- [Verifying dev mode is working](#verifying-dev-mode-is-working)
- [IDE setup](#ide-setup)
  - [VSCode](#vscode)
  - [PHPStorm](#phpstorm)
- [Triggering the debugger](#triggering-the-debugger)
  - [Browser debugging](#browser-debugging)
  - [CLI script debugging](#cli-script-debugging)
- [Directory structure](#directory-structure)
- [Log locations](#log-locations)
- [Accessing the database](#accessing-the-database)
- [Troubleshooting](#troubleshooting)
- [Updating MediaWiki code](#updating-mediawiki-code)
- [Performance considerations](#performance-considerations)

---

## Features

- **Live Code Editing**: MediaWiki code is extracted to a local directory and mounted into the container. Changes appear immediately without rebuilding.
- **Xdebug Integration**: Step through PHP code, set breakpoints, and inspect variables.
- **IDE Support**: Pre-configured for both VSCode and PHPStorm.

---

## Enabling dev mode

First create a Canasta installation, then enable dev mode:

```bash
# Create an installation
canasta create -i mydev -w mywiki -n localhost -a admin

# Enable development mode
canasta devmode enable -i mydev
```

You can also specify a specific Canasta image tag when creating. Use `--dev-tag` to control which Canasta image tag to use:
- Without `--dev-tag` — Uses the `latest` image (default)
- `--dev-tag dev-branch` — Uses the specified image tag

```bash
canasta create -i mydev -w mywiki -n localhost -a admin --dev-tag dev-branch
canasta devmode enable -i mydev
```

Enabling dev mode will:
1. Extract MediaWiki code to `mediawiki-code/` for live editing
2. Create dev mode files (Dockerfile.xdebug, docker-compose.dev.yml, xdebug.ini)
3. Build an Xdebug-enabled Docker image
4. Create IDE configuration files
5. Restart the installation with dev mode enabled

### Available image tags

- `latest` - Latest stable release (default)
- Any tag from [ghcr.io/canastawiki/canasta](https://github.com/CanastaWiki/Canasta/pkgs/container/canasta)

---

## Building from local source

For testing changes to Canasta or CanastaBase before they're published, you can build from local source repositories:

```bash
canasta create -i mydev -w mywiki -n localhost -a admin --build-from ~/canasta-repos
```

The `--build-from` flag expects a directory containing:
- `Canasta/` — Required. The Canasta repository with a Dockerfile. Fails if not found.
- `CanastaBase/` — Optional. If present, CanastaBase is built first and used as the base image for Canasta. If not found, uses the published CanastaBase image.
- `Canasta-DockerCompose/` — Optional. If present, the docker-compose stack files are copied from here instead of cloning from GitHub.

This will:
1. Build CanastaBase locally (if the directory exists) → `canasta-base:local`
2. Build Canasta using the local or published base image → `canasta:local`
3. Copy docker-compose files from local Canasta-DockerCompose (if exists) or clone from GitHub
4. Continue with normal installation

### Combining with dev mode

You can enable dev mode after building from source:

```bash
canasta create -i mydev -w mywiki -n localhost -a admin --build-from ~/canasta-repos
canasta devmode enable -i mydev
```

The dev mode enable command will detect the locally built image from the `.env` file and use it for code extraction and xdebug image building.

**Note:** `--dev-tag` and `--build-from` are mutually exclusive since `--build-from` builds its own image.

### Switching back to upstream images

When you use `--build-from`, the CLI sets `CANASTA_IMAGE=canasta:local` in the installation's `.env` file so Docker Compose uses the locally-built image. This means:

- `canasta upgrade` will **not** pull newer upstream images — the installation stays on the local build
- `docker compose pull` will silently skip the web service since `canasta:local` is not in any registry

To switch back to the upstream Canasta image:

1. Remove the `CANASTA_IMAGE` line from `.env` (or set it to the desired upstream image, e.g. `CANASTA_IMAGE=ghcr.io/canastawiki/canasta:latest`)
2. Pull the upstream image and restart:
   ```bash
   canasta upgrade
   ```

---

## Disabling dev mode

To disable dev mode and restore normal operation:

```bash
canasta devmode disable -i myinstance
```

This restores extensions and skins as real directories and restarts without Xdebug. The dev mode files (Dockerfile.xdebug, docker-compose.dev.yml, mediawiki-code/) are left in place so you can re-enable dev mode later without re-extracting code.

---

## Verifying dev mode is working

After enabling dev mode, verify the setup by running these commands from the installation directory:

1. **Check containers are running with the dev image**:
   ```bash
   docker compose ps
   ```
   The web service should show `canasta-xdebug:local` as the image.

2. **Verify code mounting works**:
   ```bash
   # Add a comment to mediawiki-code/index.php
   # Refresh the browser - changes appear immediately without restart
   ```

3. **Check Xdebug is loaded**:
   ```bash
   docker compose exec web php -m | grep xdebug
   ```
   Should output: `xdebug`

---

## IDE setup

### VSCode

A `.vscode/launch.json` file is automatically created. To start debugging:

1. Open the installation's root directory in VSCode (not the `mediawiki-code/` subdirectory) — the path mappings require this
2. Install the **PHP Debug** extension (by Xdebug)
3. Go to **Run and Debug** (Ctrl+Shift+D / Cmd+Shift+D)
4. Select **"Listen for Xdebug"** and click the play button
5. Set breakpoints in files under `mediawiki-code/`
6. Access your wiki in the browser - VSCode will break at your breakpoints

### PHPStorm

Configuration files are automatically created in the `.idea/` directory. To start debugging:

1. Open the installation's root directory in PHPStorm (not the `mediawiki-code/` subdirectory) — the path mappings require this
2. Go to **Run** → **Edit Configurations**
3. The **"Listen for Xdebug"** configuration should already be available
4. Click the **phone/listen icon** in the toolbar (or **Run** → **Start Listening for PHP Debug Connections**)
5. Set breakpoints in files under `mediawiki-code/`
6. Access your wiki in the browser - PHPStorm will break at your breakpoints

---

## Triggering the debugger

By default, Xdebug uses trigger mode (`xdebug.start_with_request=trigger`), meaning it only starts a debug session when explicitly requested. This prevents background tasks (like health checks) from constantly triggering breakpoints.

### Browser debugging

You must set the `XDEBUG_TRIGGER` cookie in your browser. Query parameters (`?XDEBUG_TRIGGER=1`) do not work with PHP-FPM—only cookies are recognized.

#### Option 1: Set cookie in DevTools (most reliable)

**Chrome:**
1. Open DevTools (F12 or Cmd+Option+I)
2. Go to **Application** tab → **Cookies** → your site (e.g., `https://localhost`)
3. Double-click an empty row at the bottom of the cookie table
4. Set:
   - Name: `XDEBUG_TRIGGER`
   - Value: `1`
   - Path: `/`
5. Press Enter

The cookie persists across page refreshes until you delete it or close the browser.

**Safari:**
1. Open Web Inspector (Cmd+Option+I)
2. Go to **Storage** tab → **Cookies**
3. Right-click and add a cookie with Name: `XDEBUG_TRIGGER`, Value: `1`

#### Option 2: Set cookie via console

In the browser's JavaScript console, run:
```javascript
document.cookie = "XDEBUG_TRIGGER=1; path=/; secure";
```

To remove the cookie:
```javascript
document.cookie = "XDEBUG_TRIGGER=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
```

#### Option 3: Bookmarklets

Create bookmarks with these URLs. Note: Some browsers may block bookmarklets from modifying cookies on HTTPS pages.

**Start Debugging:**
```javascript
javascript:(function(){document.cookie='XDEBUG_TRIGGER=1;path=/;secure';alert('Xdebug enabled')})();
```

**Stop Debugging:**
```javascript
javascript:(function(){document.cookie='XDEBUG_TRIGGER=;path=/;expires=Thu, 01 Jan 1970 00:00:00 GMT';alert('Xdebug disabled')})();
```

### CLI script debugging

To debug command-line scripts, set the `XDEBUG_TRIGGER` environment variable. Run these commands from the installation directory:

```bash
docker compose exec -e XDEBUG_TRIGGER=1 web php maintenance/run.php someScript
```

---

## Directory structure

After enabling dev mode on an installation:

```
installation/
├── .env                      # Environment variables (includes CANASTA_IMAGE)
├── .vscode/
│   └── launch.json           # VSCode debug configuration
├── .idea/
│   ├── php.xml               # PHPStorm server configuration
│   └── runConfigurations/
│       └── Listen_for_Xdebug.xml  # PHPStorm run configuration
├── config/
│   ├── wikis.yaml            # Wiki farm configuration
│   ├── Caddyfile             # Generated reverse proxy config (do not edit)
│   ├── Caddyfile.site         # User customizations for Caddy site block
│   ├── xdebug.ini            # Xdebug configuration
│   ├── admin-password_{wikiid}  # Generated admin password per wiki
│   └── settings/
│       ├── global/           # PHP settings loaded for all wikis
│       │   └── *.php
│       └── wikis/
│           └── {wikiid}/     # PHP settings loaded for a specific wiki
│               └── *.php
├── docker-compose.yml
├── docker-compose.override.yml
├── docker-compose.dev.yml    # Dev mode compose overlay
├── Dockerfile.xdebug         # Builds xdebug-enabled image
├── extensions/               # Symlink → mediawiki-code/user-extensions/ (in dev mode)
├── skins/                    # Symlink → mediawiki-code/user-skins/ (in dev mode)
└── mediawiki-code/           # Extracted MediaWiki code (mounted to /var/www/mediawiki/w)
    ├── index.php
    ├── canasta-extensions/   # Bundled extension source code
    ├── canasta-skins/        # Bundled skin source code
    ├── extensions/           # Symlinks to canasta-extensions/* and user-extensions/*
    ├── skins/                # Symlinks to canasta-skins/* and user-skins/*
    ├── user-extensions/      # User extensions (real directory, same content as extensions/)
    ├── user-skins/           # User skins (real directory, same content as skins/)
    └── ...
```

### How extension symlinks work

The extracted code preserves the container's symlink structure. The `mediawiki-code/extensions/` directory contains relative symlinks like:

- `VisualEditor` → `../canasta-extensions/VisualEditor` (bundled extension)
- `MyExtension` → `../user-extensions/MyExtension` (user extension)

In dev mode, the CLI consolidates user extensions into `mediawiki-code/`:
1. Copies any existing extensions from `extensions/` to `mediawiki-code/user-extensions/`
2. Replaces `extensions/` with a symlink to `mediawiki-code/user-extensions/`
3. Same for `skins/` → `mediawiki-code/user-skins/`

This ensures:
- Symlinks in `mediawiki-code/extensions/` resolve identically on host and container
- Simple IDE path mapping: `mediawiki-code/` ↔ `/var/www/mediawiki/w/`
- Breakpoints work in both bundled and user extensions

---

## Editing user extensions (best practices)

In dev mode, `extensions/` is a symlink to `mediawiki-code/user-extensions/`. This means **both paths point to the same files**:

```
extensions/MyExtension/          ← Same files
mediawiki-code/user-extensions/MyExtension/  ← Same files
```

### Where to edit

You can edit user extension files in **either location** — they're the same directory:

| Path | Description |
|------|-------------|
| `extensions/MyExtension/` | Convenient if you're used to non-dev mode |
| `mediawiki-code/user-extensions/MyExtension/` | Shows the true location in dev mode |

### Setting breakpoints

For IDE path mapping to work correctly, set breakpoints in files under `mediawiki-code/`:
- `mediawiki-code/user-extensions/MyExtension/includes/MyHooks.php` ✓
- `mediawiki-code/extensions/MyExtension/` (symlink path also works)

### Adding new extensions

1. Copy your extension to `extensions/` (or `mediawiki-code/user-extensions/`)
2. Restart the container: `canasta restart -i myinstance`
3. The container's `create-symlinks.sh` creates `mediawiki-code/extensions/MyExtension → ../user-extensions/MyExtension`
4. Enable the extension in LocalSettings.php

### When dev mode is disabled

When you disable dev mode (`canasta devmode disable -i myinstance`), the CLI:
1. Removes the `extensions/` symlink
2. Copies content from `mediawiki-code/user-extensions/` back to a real `extensions/` directory
3. Leaves `mediawiki-code/` in place (not volumed in, but available for reference)

Your user extensions are preserved in both modes.

### When dev mode is re-enabled

When you re-enable dev mode (`canasta devmode enable -i myinstance`) after having disabled it:
1. Extensions in `extensions/` **take precedence** over `mediawiki-code/user-extensions/`
2. Any changes made to `extensions/` while in non-dev mode are synced to `mediawiki-code/user-extensions/`
3. The `extensions/` directory becomes a symlink again

**Best practice:** Always edit user extensions in `extensions/` regardless of mode. This ensures your changes are preserved when switching between dev and non-dev modes.

---

## Log locations

For general log file locations (MediaWiki debug log, Apache error log, etc.), see [Troubleshooting: Log file locations](troubleshooting.md#log-file-locations).

### Xdebug log

In dev mode, Xdebug writes connection attempts to its own log file:

```bash
docker compose exec web tail -f /var/log/mediawiki/php-xdebug.log
```

This log is useful for diagnosing why breakpoints aren't being hit:
- "Trigger value not found" — Cookie not set or not being sent
- "Could not connect to debugging client" — IDE not listening
- "Connecting to configured address" — Working correctly

---

## Accessing the database

To debug data issues, connect to MySQL from the installation directory:

```bash
docker compose exec db mysql -uroot -pmediawiki
```

Common queries:
```sql
SHOW DATABASES;                    -- List all wiki databases
USE mywiki;                        -- Switch to a wiki database
SHOW TABLES;                       -- List tables
SELECT * FROM user LIMIT 5;        -- Query users
```

---

## Troubleshooting

### Breakpoints not hitting

1. **Check IDE is listening**: Ensure the debug listener is active (phone icon should be green/active)
2. **Check the XDEBUG_TRIGGER cookie is set**: In DevTools → Application → Cookies, verify the cookie exists
3. **Check path mappings**: Verify local `mediawiki-code` maps to `/var/www/mediawiki/w`
4. **Check Xdebug log** (run from the installation directory):
   ```bash
   docker compose exec web tail -20 /var/log/mediawiki/php-xdebug.log
   ```
   - "Trigger value not found" = Cookie not set or not being sent
   - "Could not connect to debugging client" = IDE not listening
   - "Connecting to configured address" = Working correctly

### PHPStorm detaches immediately

If you see "Cannot bind file" errors, your path mappings are incorrect. Ensure:
- Local path: `/path/to/installation/mediawiki-code`
- Remote path: `/var/www/mediawiki/w`

---

## Updating MediaWiki code

The extracted `mediawiki-code/` is a snapshot from when dev mode was enabled. To re-extract the code from the Canasta container:

```bash
# Stop the instance
canasta stop -i myinstance

# Remove the extracted code
rm -rf mediawiki-code/

# Re-enable dev mode (this will re-extract the code)
canasta devmode enable -i myinstance
```

This preserves your wikis, configuration, and database while updating the MediaWiki code.

**Note**: Your local edits in `mediawiki-code/` will be lost when regenerating. Consider committing changes to a git repository before regenerating. User extensions in `extensions/` are preserved.

---

## Performance considerations

Dev mode with Xdebug is slower than production mode due to:
- Xdebug overhead (even when not actively debugging)
- Bind-mounted code (slightly slower file I/O on some systems)

For performance testing, disable dev mode:
```bash
canasta devmode disable -i myinstance
```
