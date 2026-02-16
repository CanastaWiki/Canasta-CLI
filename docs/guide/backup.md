# Backup and restore

Canasta includes backup and restore support powered by [restic](https://restic.net). Backups can be stored on any restic-compatible backend (AWS S3, Google Cloud Storage, Azure Blob Storage, Backblaze B2, local filesystem, SFTP, and more).

## What gets backed up

Each backup includes:

- **Database** — a full MySQL dump
- **Configuration** — the `config/` directory (Caddyfile, settings, wikis.yaml)
- **Extensions and skins** — the `extensions/` and `skins/` directories
- **Uploaded files** — the `images/` directory

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
canasta backup schedule -i myinstance "0 2 * * *"

# Every 6 hours
canasta backup schedule -i myinstance "0 */6 * * *"
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
