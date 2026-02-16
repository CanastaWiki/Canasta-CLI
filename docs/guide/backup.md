# Backup and restore

Canasta includes backup and restore support powered by [restic](https://restic.net). Backups can be stored on any restic-compatible backend (AWS S3, Google Cloud Storage, Azure Blob Storage, Backblaze B2, local filesystem, SFTP, and more).

## Setup

1. Add these environment variables to your `.env` file:
```
RESTIC_REPOSITORY=s3:s3.amazonaws.com/your-bucket-name
RESTIC_PASSWORD=your-restic-password
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

2. Initialize the backup repository:
```bash
canasta backup init -i myinstance
```

Once set up, see the [CLI Reference](../cli/canasta_backup.md) for the full list of backup subcommands, flags, and usage examples.
