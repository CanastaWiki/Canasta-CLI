# Backup and restore

Canasta includes backup and restore support powered by [restic](https://restic.net). Backups can be stored on any restic-compatible backend (AWS S3, Google Cloud Storage, Azure Blob Storage, Backblaze B2, local filesystem, SFTP, and more).

## What gets backed up

Each backup includes:

- **Database** — a full MariaDB dump of each wiki's database
- **Configuration** — the `config/` directory (Caddyfile, settings, wikis.yaml)
- **Extensions and skins** — the `extensions/` and `skins/` directories
- **Uploaded files** — the `images/` directory
- **Public assets** — the `public_assets/` directory
- **Environment and overrides** — `.env`, `docker-compose.override.yml`, and `my.cnf` (if present)

## Setup

### 1. Configure a storage backend

Add the `RESTIC_REPOSITORY` and `RESTIC_PASSWORD` variables to your installation's `.env` file. The repository URL format depends on the backend you choose:

**AWS S3:**
```
RESTIC_REPOSITORY=s3:s3.amazonaws.com/your-bucket-name
RESTIC_PASSWORD=your-restic-password
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

**Local filesystem:**
```
RESTIC_REPOSITORY=/path/to/backup/repo
RESTIC_PASSWORD=your-restic-password
```

**SFTP:**
```
RESTIC_REPOSITORY=sftp:user@host:/path/to/repo
RESTIC_PASSWORD=your-restic-password
```

See the [restic documentation](https://restic.readthedocs.io/en/latest/030_preparing_a_new_repo.html) for the full list of supported backends and their URL formats.

### 2. Initialize the repository

This must be done once before creating any backups:

```bash
canasta backup init -i myinstance
```

## Common operations

### Creating a backup

```bash
canasta backup create -i myinstance -t before-upgrade
```

The `-t` flag assigns a descriptive tag to the backup. Tags help identify backups later — use names like `before-upgrade`, `weekly-2024-03-15`, or `pre-migration`.

### Listing backups

```bash
canasta backup list -i myinstance
```

This displays all snapshots in the repository with their IDs, timestamps, and tags.

### Restoring a backup

```bash
canasta backup restore -i myinstance -s abc123
```

Replace `abc123` with the snapshot ID from `canasta backup list`. By default, a safety backup is taken before restoring. To skip this:

```bash
canasta backup restore -i myinstance -s abc123 --skip-safety-backup
```

### Restoring a single wiki

To restore only one wiki from a backup without affecting the rest of the installation:

```bash
canasta backup restore -i myinstance -s abc123 -w wiki2
```

This restores the wiki's database, per-wiki settings (`config/settings/wikis/{id}/`), images (`images/{id}/`), and public assets (`public_assets/{id}/`). Shared files like global config, extensions, and skins are left untouched.

The wiki ID must exist in the current installation's `wikis.yaml`.

### Restoring a backup to a different instance

You can restore a full backup to a different Canasta instance. This is useful for cloning an installation, migrating to a new server, or inspecting the contents of a backup without affecting your running instance.

First, create a new instance and configure it with the same backup repository credentials in its `.env` file. Then restore:

```bash
canasta backup restore -i other-instance -s abc123
```

The target instance will receive all files and databases from the snapshot, replacing its current contents.

### Inspecting a backup

To see what files are in a specific snapshot:

```bash
canasta backup files -i myinstance -s abc123
```

### Comparing two backups

```bash
canasta backup diff -i myinstance --snapshot1 abc123 --snapshot2 def456
```

### Deleting a backup

```bash
canasta backup delete -i myinstance -s abc123
```

### Scheduling recurring backups

Set up automatic backups using a cron expression:

```bash
# Daily at 2:00 AM
canasta backup schedule set -i myinstance "0 2 * * *"

# Every 6 hours
canasta backup schedule set -i myinstance "0 */6 * * *"
```

If you reschedule with a new expression, the existing schedule is replaced. To schedule multiple times, combine them in one expression (e.g., `"0 0 * * 2,5"` for Tuesdays and Fridays).

Backup output is logged to `backup.log` in the installation directory. The log is automatically rotated when it exceeds 10 MB — the previous log is kept as `backup.log.1`.

To view the current schedule:

```bash
canasta backup schedule list -i myinstance
```

To remove a scheduled backup:

```bash
canasta backup schedule remove -i myinstance
```

### Repository maintenance

Check the repository for errors:

```bash
canasta backup check -i myinstance
```

If a backup operation was interrupted and left the repository locked:

```bash
canasta backup unlock -i myinstance
```

## Further reading

- [CLI Reference](../cli/canasta_backup.md) — full list of subcommands, flags, and options
- [restic documentation](https://restic.readthedocs.io/) — details on storage backends, encryption, and advanced usage
