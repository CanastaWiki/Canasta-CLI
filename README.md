# Canasta CLI - Recent Enhancements & New Commands for wiki farm support

We're excited to introduce a series of enhancements and new commands to the Canasta CLI. These changes aim to provide users with more flexibility and power in managing their MediaWiki instances and wiki farms.

## Table of Contents
- [Installation](#installation)
- [Enhancements](#enhancements)
  - [create](#create)
  - [extension](#extension)
  - [skin](#skin)
- [New Commands](#new-commands)
  - [add](#add)
  - [remove](#remove)


## Enhancements

### create
**Description:** Creates a Canasta installation. Enhanced to support wiki farm setup with the `-f` flag.

**Usage:**
sudo canasta create [flags]
- `-p, --path`: Canasta directory.
- `-o, --orchestrator`: Orchestrator to use for installation (default: "docker-compose").
- `-i, --id`: Canasta instance ID.
- `-w, --wiki`: Name of the wiki.
- `-n, --domain-name`: Domain name (default: "localhost").
- `-a, --WikiSysop`: Initial wiki admin username.
- `-s, --password`: Initial wiki admin password.
- `-f, --yamlfile`: Initial wiki yaml file for wiki farm setup.
- `-k, --keep-config`: Keep the config files on installation failure.
- `-r, --override`: Name of a file to copy to docker-compose.override.yml.
- `--rootdbpass`: Prompt for the password for the root database user (default: "mediawiki").
- `--wikidbuser`: The database user to use for normal operations (default: "root").
- `--wikidbpass`: Prompt for the password for the database user to use for normal operations (default: "mediawiki").

**YAML Format for Wiki Farm:**
To create a wiki farm, you first need to create a YAML file with the following format:
```yaml
wikis:
  - id: [WIKI_ID] # Example: "mywiki1"
    url: [WIKI_URL] # Example: "mywiki1.example.com"
```

Then run the following:

```bash
sudo canasta create -f [yamlfile] # Example: "wikis.yaml"
```

### extension
**Description:** Manage Canasta extensions. Enhanced to target a specific wiki within the farm using the `-w` flag.

**Subcommands:**
- `list`: Lists all the installed Canasta extensions.
- `enable`: Enables specified extensions.
- `disable`: Disables specified extensions.

**Usage:**
```bash
sudo canasta extension [subcommand] [flags]
```
**Flags:**
- `-i, --id`: Specifies the Canasta instance ID.
- `-p, --path`: Specifies the Canasta installation directory.
- `-w, --wiki`: Specifies the ID of a specific wiki within the Canasta farm.
- `-v, --verbose`: Enables verbose output.

## New Commands

### add
**Description:** Adds a new wiki to a Canasta instance.

**Usage:**
```bash
sudo canasta add [flags]
```
**Flags:**
- `-w, --wiki`: ID of the new wiki.
- `-u, --url`: URL of the new wiki.
- `-s, --site-name`: Name of the new wiki site.
- `-p, --path`: Path to the new wiki.
- `-i, --id`: Canasta instance ID.
- `-o, --orchestrator`: Orchestrator to use for installation (default: "docker-compose").
- `-d, --database`: Path to the existing database dump.

### remove
**Description:** Removes a wiki from a Canasta instance.

**Usage:**
```bash
sudo canasta remove [flags]
```
**Flags:**
- `-w, --wiki`: ID of the wiki to be removed.
- `-p, --path`: Path to the wiki.
- `-i, --id`: Canasta instance ID.

