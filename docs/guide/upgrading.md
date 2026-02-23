# Upgrading

This guide covers upgrading Canasta installations and migrating from legacy (non-CLI) setups.

## Contents

- [How upgrading works](#how-upgrading-works)
- [Pre-upgrade checklist](#pre-upgrade-checklist)
- [Version notes](#version-notes)
- [Migrating from a legacy installation](#migrating-from-a-legacy-installation)

---

## How upgrading works

The `canasta upgrade` command performs the following steps:

1. Pulls the latest Canasta Docker image (or rebuilds it if the installation was created with `--build-from`)
2. Restarts the containers with the new image
3. Runs `maintenance/update.php` automatically to apply any database schema changes

To upgrade a single installation:

```bash
canasta upgrade -i myinstance
```

To upgrade all installations at once:

```bash
canasta upgrade --all
```

The CLI also updates itself during the upgrade process, so you always have the latest CLI version.

## Pre-upgrade checklist

1. **Back up your data** before every upgrade:
   ```bash
   canasta backup create -t "pre-upgrade-$(date +%Y%m%d)" -i myinstance
   ```
2. Review the [Canasta release notes](https://github.com/CanastaWiki/Canasta/releases) for any breaking changes.
3. Ensure your user has the required group memberships (see [Version notes](#version-notes) below if upgrading from an older release).

## Version notes

The CLI's relationship with `sudo` changed over several releases:

| Version | Change |
|---------|--------|
| **v1.48.0** | CLI began handling `www-data` file ownership internally, enabling commands to run without `sudo`. |
| **v1.65.0** | `sudo` removed from installer messages and documentation examples. |
| **v1.127.0** | `www-data` group requirement formally documented. |

### Upgrading from pre-v1.48.0

If you previously ran commands with `sudo canasta ...`, you should:

1. Add your user to the `docker` and `www-data` groups:
   ```bash
   sudo usermod -aG docker,www-data $USER
   ```
2. Log out and log back in for the group changes to take effect.
3. Stop using `sudo` with Canasta CLI commands.

See the [installation guide](../installation.md#linux) for full details on Linux group requirements.

## Migrating from a legacy installation

If you have an existing MediaWiki installation that was **not** created with the Canasta CLI (for example, a manual Docker Compose setup or a bare-metal install), you can migrate it to a CLI-managed Canasta installation.

### 1. Export your database

Use `mysqldump` (or the equivalent for your database engine) to create a SQL dump of your wiki database:

```bash
mysqldump -u root -p wikidb > wikidb.sql
```

### 2. Create a new Canasta installation with the database dump

```bash
canasta create -i myinstance -w mywiki -a admin -n example.com -d wikidb.sql
```

The `-d` flag imports the SQL dump during installation. The `admin` account will be created only if it does not already exist in the imported database.

### 3. Copy your settings files

Copy your existing PHP settings files (e.g., `LocalSettings.php` customizations) into the new installation's global settings directory:

```bash
cp MySettings.php /path/to/myinstance/config/settings/global/
```

If you are running a wiki farm, place per-wiki settings in `config/settings/wikis/<wikiid>/`.

### 4. Copy uploaded images

Copy your uploaded files into the installation's `images/<wiki-id>/` directory:

```bash
cp -r /old/wiki/images/* /path/to/myinstance/images/<wiki-id>/
```

### 5. Copy Caddyfile customizations

If you had custom web server rules, copy them to the Canasta Caddyfile locations:

- `config/Caddyfile.site` — per-site directives (headers, redirects, etc.)
- `config/Caddyfile.global` — global Caddy options

Do **not** edit `config/Caddyfile` directly, as it is regenerated when wikis change.

### 6. Copy custom extensions and skins

If you have extensions or skins that are **not** bundled with Canasta, copy them into the installation:

```bash
cp -r /old/wiki/extensions/MyExtension /path/to/myinstance/extensions/
cp -r /old/wiki/skins/MySkin /path/to/myinstance/skins/
```

### 7. Carry over `.env` settings

Review your old configuration and carry over any custom environment variables (database credentials, `MW_SITE_SERVER`, etc.) into the new installation's `.env` file at the root of the installation directory.

### 8. Restart and verify

Restart the installation and verify that everything is working:

```bash
canasta restart -i myinstance
```

The `update.php` maintenance script runs automatically on container startup, so database schema migrations will be applied.
