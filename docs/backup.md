# Backup and restore with restic

Canasta includes integration with [restic](https://restic.net) for automated backups to AWS S3-compatible storage.

## Setup

1. Add these environment variables to your `.env` file:
```
AWS_S3_API=s3.amazonaws.com
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_S3_BUCKET=your-bucket-name
RESTIC_PASSWORD=your-restic-password
```

2. Initialize the restic repository:
```bash
sudo canasta restic init -i mywiki
```

Once set up, see the [CLI Reference](cli/canasta_restic.md) for the full list of restic subcommands, flags, and usage examples.
