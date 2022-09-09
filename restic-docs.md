# Restic Documentation
* More about restic at https://restic.net
* The current version is configured for using AWS S3 based repositories
* It uses restic's [dockerized binary](https://hub.docker.com/r/restic/restic)

## How to get started
1. Add these environment variables to your Canasta's `.env`.Follow the steps at https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html#cli-configure-quickstart-creds-create to obtain ACCESS_KEY_ID and SECRET_ACESS_KEY
```
AWS_S3_API=s3.amazonaws.com
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_S3_BUCKET=
RESTIC_PASSWORD=
```
2. When using restic for the first time in a Canasta installation please run the following command to initialize a restic repo in AWS S3 Bucket specified in the `.env` file.
```
sudo canata restic init -i canastaId
```
Now you should be able to use any of the available commands.

## Available Commands:
  ```
  check         Check restic snapshots
  diff          Show difference between two snapshots
  forget        Forget restic snapshots
  init          Initialize a restic repo
  list          List files in a snapshost
  restore       Restore restic snapshot
  take-snapshot Take restic snapshots
  unlock        Remove locks other processes created
  view          View restic snapshots
  ```
Use "sudo canasta restic [command] --help" for more information about a command.
