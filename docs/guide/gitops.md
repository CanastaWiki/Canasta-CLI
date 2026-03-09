# GitOps configuration management

Canasta supports git-based configuration management through the `canasta gitops` command group. Your installation's configuration files are stored in a private Git repository with encrypted secrets, providing change history, easy rollback, and optional multi-server deployments.

## Overview

In the simplest case, a single server uses gitops purely for version-controlled configuration backup — every change is committed and pushed to a remote repository. No pull requests, no multi-server coordination — just `canasta gitops push` after making changes.

The same architecture extends to multi-server deployments. Changes are made on a source server, tested, pushed to the repo with an optional pull request for peer review, and then pulled onto production servers.

!!! note
    Gitops manages **configuration only** — it does not back up databases or uploaded files. Use [`canasta backup`](backup.md) separately for that.

## Prerequisites

The following tools must be installed:

- [git-crypt](https://github.com/AGWA/git-crypt) — transparent encryption of secrets in the git repo
    - **macOS:** `brew install git-crypt`
    - **Ubuntu/Debian:** `sudo apt install git-crypt`
    - **RHEL/Fedora:** `sudo dnf install git-crypt`
- [gh](https://cli.github.com/) (GitHub CLI) — only required when `pull_requests: true` is set in `hosts.yaml`

`canasta gitops init` checks for these and provides instructions if any are missing.

## What gets tracked

The git repository contains:

- **Configuration files** — `config/` directory (wikis.yaml, Caddyfile customizations, PHP settings)
- **Environment template** — `env.template` with `{{placeholders}}` for host-specific values
- **Per-host variables** — `hosts/{name}/vars.yaml` with secrets and host-specific values (encrypted by git-crypt)
- **Host inventory** — `hosts.yaml` defining all servers and their roles
- **Extensions and skins** — tracked as git submodules pinned to specific versions, or as regular files for custom extensions without their own repository
- **Custom files** — `custom/` directory for Dockerfiles, scripts, or other deployment files
- **Orchestrator overrides** — `docker-compose.override.yml` (if present)
- **Public assets** — `public_assets/` directory (logos, favicons)

### What is NOT tracked (gitignored)

- `.env` — generated from template + vars at deploy time
- `config/admin-password_*` — generated from vars at deploy time
- `docker-compose.yml` — managed by the Canasta CLI
- `config/Caddyfile` — auto-generated from wikis.yaml on restart
- `images/` — uploaded files (covered by `canasta backup`)

## Repository structure

```
canasta-config/
├── .gitattributes              # git-crypt filter rules
├── .gitignore
├── custom-keys.yaml            # user-defined host-specific .env keys (optional)
├── env.template                # shared .env template with {{placeholders}}
├── config/
│   ├── wikis.yaml
│   ├── Caddyfile.site
│   ├── Caddyfile.global
│   └── settings/
│       ├── global/
│       │   └── *.php
│       └── wikis/
│           └── {wiki-id}/
│               └── *.php
├── custom/                     # user files (Dockerfiles, extra configs, scripts)
├── extensions/                 # git submodules (or regular files for custom extensions)
├── skins/                      # git submodules (or regular files for custom skins)
├── public_assets/
├── docker-compose.override.yml # if used
├── hosts/                      # per-host variables (encrypted by git-crypt)
│   └── myserver/
│       └── vars.yaml
└── hosts.yaml                  # host inventory and settings
```

## Initial setup

Gitops works with any existing Canasta installation — single wiki or wiki farm. You don't need to reinstall or recreate anything. The `init` command examines your running installation and builds the gitops repository around it.

All gitops commands accept `-i <id>` to specify the installation by its Canasta ID. If omitted, the command uses the current working directory.

### 1. Verify your installation

Make sure your installation is working and that `.env`, `config/`, and any extensions/skins are in their final state. Gitops will snapshot the current configuration as its starting point.

If you have a wiki farm, ensure `config/wikis.yaml` and all per-wiki settings under `config/settings/` are in place. Admin passwords (`config/admin-password_*`) are automatically captured into the encrypted per-host vars.

If you don't have an installation yet:

```bash
canasta create -i mywiki -w main -n wiki.example.com
```

### 2. Check for custom secrets

Before initializing gitops, review your `.env` file for any secrets or host-specific values beyond the built-in set. Gitops automatically extracts the following into encrypted per-host variables:

- **Database and MediaWiki secrets:** `MYSQL_PASSWORD`, `WIKI_DB_PASSWORD`, `MW_SECRET_KEY`
- **Backup credentials:** `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, and any key starting with `AWS_`, `AZURE_`, `B2_`, `GOOGLE_`, `OS_`, `ST_`, or `RCLONE_`
- **Host-specific values:** `MW_SITE_SERVER`, `MW_SITE_FQDN`, `HTTP_PORT`, `HTTPS_PORT`

Any `.env` key **not** in this list is committed as a literal value in `env.template`, which is **not encrypted**. If you have additional secrets (e.g., `getenv('MY_API_KEY')` in PHP settings, SMTP credentials), create a `custom-keys.yaml` file in the installation directory before running init:

```yaml
keys:
  - MY_API_KEY
  - SMTP_PASSWORD
```

Then set their values:

```bash
canasta config set -i mywiki MY_API_KEY=... SMTP_PASSWORD=...
```

If you don't have any custom secrets beyond the built-in set, you can skip this step.

### 3. Initialize gitops

```bash
canasta gitops init -i mywiki -n myserver --repo git@github.com:yourorg/mywiki-config.git --key /path/to/gitops-key
```

To require pull requests for all changes instead of pushing directly to main:

```bash
canasta gitops init -i mywiki -n myserver --repo git@github.com:yourorg/mywiki-config.git --key /path/to/gitops-key --pull-requests
```

The remote repository must be empty (no commits, no README). Create an empty repository on GitHub/GitLab first, then pass its URL with `--repo`. The `--key` flag specifies where to export the git-crypt symmetric key.

This bootstraps a new gitops repository from the existing installation:

1. Initializes a git repo in the installation directory
2. Sets up `.gitignore` and `.gitattributes`
3. Initializes git-crypt and exports the symmetric key
4. Creates `env.template` by extracting the current `.env` and replacing host-specific values with `{{placeholders}}`
5. Creates `hosts.yaml` with this server as the first entry
6. Creates `hosts/myserver/vars.yaml` with the actual values extracted from `.env` and admin password files
7. Converts user-installed extensions and skins to git submodules
8. Makes an initial commit
9. Pushes to the remote

**Store the exported git-crypt key securely** — it is needed to unlock the repo on other servers and must never be committed to the repo.

## Environment template and variables

The `env.template` is the single source of truth for what configuration exists. Host-specific values (secrets, domain names, ports) are replaced with `{{placeholders}}`:

```
MW_SITE_SERVER={{mw_site_server}}
MW_SITE_FQDN={{mw_site_fqdn}}
MYSQL_PASSWORD={{mysql_password}}
MW_SECRET_KEY={{mw_secret_key}}
MW_DB_NAME=mediawiki
MW_SITE_NAME=My Wiki
```

Each host's `vars.yaml` supplies the actual values:

```yaml
# hosts/myserver/vars.yaml
mw_site_fqdn: wiki.example.com
mysql_password: "my-db-pass"
mw_secret_key: "abc123..."
admin_password_main: "my-admin-pass"
```

At deploy time, `canasta gitops pull` renders the template with the host's vars to produce `.env`, and writes `config/admin-password_*` files from the corresponding vars.

!!! warning "Do not edit `.env` directly"
    The `.env` file is regenerated from `env.template` + `vars.yaml` on every pull. Manual edits to `.env` will be overwritten. To change a host-specific value, update `hosts/{name}/vars.yaml`. To change the structure of the `.env` (add or remove keys), edit `env.template`.

### Built-in placeholder keys

The following `.env` keys are automatically converted to placeholders:

**Secrets** (differ per host):
`MYSQL_PASSWORD`, `WIKI_DB_PASSWORD`, `MW_SECRET_KEY`, `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`

**Backup backend credentials** (auto-detected by prefix):
Any key starting with `AWS_`, `AZURE_`, `B2_`, `GOOGLE_`, `OS_`, `ST_`, or `RCLONE_`

**Host-specific values:**
`MW_SITE_SERVER`, `MW_SITE_FQDN`, `HTTPS_PORT`, `HTTP_PORT`

Additional keys can be added via `custom-keys.yaml`.

## Host inventory

The `hosts.yaml` file defines the deployment targets:

**Single-server** (simplest case):

```yaml
canasta_id: mywiki
hosts:
  myserver:
    role: both
```

When there is only one host, the role defaults to `both` and can be omitted.

**Multi-server with pull requests:**

```yaml
canasta_id: mywiki
pull_requests: true
hosts:
  staging:
    role: source
  production:
    role: sink
```

### Roles

Each host has a role that controls the direction of git flow:

| Role | Can push | Can pull | Use case |
|------|----------|----------|----------|
| `source` | Yes | No | Staging, dev — where changes originate |
| `sink` | No | Yes | Production — receives config from the repo |
| `both` | Yes | Yes | Single server, or dual-purpose server |

Roles act as a safety guardrail — a `sink` host will refuse to push, preventing accidental commits of local drift on production.

### Pull requests setting

The `pull_requests` setting controls how `canasta gitops push` behaves:

- **`false`** (default) — commits push directly to `main`. Good for single-server setups or small teams.
- **`true`** — push creates a branch and opens a pull request for review. Requires the `gh` CLI.

## Common operations

### Changing a setting

1. Edit the settings file
2. Test the change
3. Push:

```bash
canasta gitops push -i mywiki -m "Enable VisualEditor by default"
```

If pull requests are enabled, review and merge the PR. Then on sink hosts:

```bash
canasta gitops pull -i mywiki
```

### Checking status

```bash
canasta gitops status -i mywiki
```

Shows the current host, role, commit info, uncommitted changes, and ahead/behind remote status.

### Previewing changes before pulling

```bash
canasta gitops diff -i mywiki
```

Fetches without applying and shows what files would change.

## Extensions and skins

Extensions and skins that have their own git repositories are tracked as **git submodules**, pinning each one to an exact commit. This ensures every server runs the same version and makes updates explicit and reviewable.

### How init handles extensions and skins

During `canasta gitops init`, the CLI scans the `extensions/` and `skins/` directories. Each subdirectory that contains a `.git` folder is automatically converted to a git submodule using its `origin` remote URL. The original directory is removed and re-added via `git submodule add`.

Subdirectories that are **not** git repositories — such as custom extensions you wrote yourself or copied in without cloning — are left as regular files and committed directly to the repo. They are tracked like any other file, not as submodules. See [Custom extensions](#custom-extensions-without-a-git-repo) below.

If a directory has a `.git` folder but no `origin` remote configured, it is skipped with a warning.

### Adding a new extension or skin

To add a publicly available extension:

```bash
git submodule add https://github.com/wikimedia/mediawiki-extensions-Cite.git extensions/Cite
```

Then enable it in the appropriate settings file (e.g., `config/settings/global/extensions.php`), test, and push:

```bash
canasta gitops push -i mywiki -m "Add Cite extension"
```

On sink hosts after pulling, run `canasta restart -i mywiki` and then `canasta maintenance update -i mywiki` to run any database migrations.

### Updating an extension or skin

```bash
cd extensions/MyExtension
git fetch && git checkout v2.0.0
cd ../..
canasta gitops push -i mywiki -m "Update MyExtension to v2.0.0"
```

The submodule reference in the parent repo now points to the new commit. On sink hosts after pulling, run `canasta maintenance update` if the extension has schema changes.

### Removing an extension or skin

Remove the submodule and its configuration:

```bash
git submodule deinit -f extensions/MyExtension
git rm -f extensions/MyExtension
```

Remove the `wfLoadExtension` or `wfLoadSkin` call from the settings file, then push.

### Submodule initialization on new servers

When a new server joins the gitops repo via `canasta gitops join`, or when an existing server runs `canasta gitops pull`, the CLI runs `git submodule update --init --recursive`. This clones all submodule repositories and checks out the exact commits recorded in the repo.

This is necessary because git does not automatically clone submodules when cloning or pulling a repository — the submodule directories would otherwise be empty.

### Converting a cloned extension to a submodule

If you cloned an extension directly with `git clone` rather than adding it as a submodule, you need to convert it before gitops can track it properly. If you haven't run `canasta gitops init` yet, the init command handles this automatically. If gitops is already initialized:

```bash
# Note the remote URL and current commit
cd extensions/MyExtension
git remote get-url origin
git rev-parse HEAD
cd ../..

# Remove the cloned directory and re-add as a submodule
rm -rf extensions/MyExtension
git submodule add https://github.com/org/MyExtension.git extensions/MyExtension

# Check out the same commit you were on
cd extensions/MyExtension
git checkout <commit-hash>
cd ../..

canasta gitops push -i mywiki -m "Convert MyExtension to submodule"
```

### Custom extensions without a git repo

Some extensions are custom code that doesn't live in a separate git repository — for example, a small extension you wrote specifically for your wiki. These are committed directly to the gitops repo as regular files, not submodules.

Since they are regular files in the repo, they are automatically synced to all servers on `canasta gitops pull` without any submodule commands. However, they cannot be independently versioned or pinned to a specific commit the way submodules can.

## Adding a server

To add a new server to an existing managed wiki farm:

### 1. Back up the existing wiki farm

On the existing server:

```bash
canasta backup create -i mywiki
```

### 2. Create and restore on the new server

```bash
canasta create -i mywiki -w main -n production.example.com
canasta backup restore -i mywiki -b /path/to/backup.tar.gz
```

This ensures the new server has all wikis and their databases. The restore gives the new server its own generated passwords (in `.env` and `config/admin-password_*`), which are then captured as that host's vars during the join step below.

### 3. Set any custom environment variables

If the repo has a `custom-keys.yaml`, set the values on the new server:

```bash
canasta config set -i mywiki MY_API_KEY=...
```

### 4. Join the gitops repo

```bash
canasta gitops join -i mywiki -n production --repo git@github.com:yourorg/mywiki-config.git --key /path/to/gitops-key
```

This clones the repo, unlocks git-crypt, adds the host to `hosts.yaml`, extracts host-specific values into `vars.yaml`, updates submodules, and pushes the new host entry back to the repo.

## Removing a server

On a source host:

1. Remove the host entry from `hosts.yaml`
2. Optionally remove the `hosts/{name}/` directory
3. Push: `canasta gitops push -i mywiki -m "Remove production-2"`

The installation on the removed server continues to function — it simply is no longer managed through gitops.

## What needs a restart?

| Change | Restart needed? |
|--------|-----------------|
| PHP settings files | No — takes effect on next request |
| wikis.yaml | Yes — Caddyfile must be regenerated |
| Caddyfile.site / Caddyfile.global | Yes — Caddy reloads on restart |
| docker-compose.override.yml | Yes |
| .env changes | Yes |
| New extension/skin | Yes — run `canasta maintenance update` |
| Extension version update (no schema change) | No |
| Extension version update (with schema change) | Run `canasta maintenance update` |

`canasta gitops pull` and `canasta gitops diff` automatically report whether a restart or maintenance update is needed.

## Secret management

Secrets are encrypted transparently using [git-crypt](https://github.com/AGWA/git-crypt). Files under `hosts/` are configured in `.gitattributes` to be encrypted on push and decrypted on pull:

```
hosts/** filter=git-crypt diff=git-crypt
```

On servers with the key, vars files are readable as plain text. On GitHub or for users without the key, they appear as encrypted blobs.

### Key management

**Symmetric key** (recommended for small teams):

A single key file is generated during `canasta gitops init`. Distribute it securely (e.g., via `scp` or a secrets manager) to each server that needs access. After unlocking, store the key outside the repo (e.g., `/etc/canasta/gitops-key`).

**GPG-based** (for larger teams):

Each team member and server has its own GPG key. Access is granted per-identity with `git-crypt add-gpg-user`. No shared key file needed, and access can be revoked individually (though re-keying is required).

| Concern | Symmetric key | GPG-based |
|---------|---------------|-----------|
| Setup complexity | Low — one key file | Higher — GPG keys for every user/server |
| Key distribution | Must securely copy the key file | No shared secret |
| Revoking access | Change key and redistribute | Remove identity and re-key |
| Best for | Small teams, few servers | Larger teams, frequent access changes |

## Workflow diagrams

**Single server or small team** (pull requests disabled):

```
[server] → edit & test → canasta gitops push → [git repo]
```

**Multi-server with review** (pull requests enabled):

```
[source] → edit & test → canasta gitops push → [PR] → review & merge → canasta gitops pull → [sink hosts]
```

## Further reading

- [CLI Reference](../cli/canasta_gitops.md) — full list of subcommands, flags, and options
- [git-crypt documentation](https://github.com/AGWA/git-crypt) — details on encryption and key management
- [Backup and restore](backup.md) — for backing up databases and uploaded files
