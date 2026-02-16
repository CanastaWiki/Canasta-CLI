# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Canasta CLI is a command-line tool for managing Canasta MediaWiki installations. It handles creation, import, backup, and management of multiple Canasta instances using Docker Compose as the orchestrator. The tool supports both single wiki installations and wiki farms (multiple wikis in one installation).

## Git Workflow

Never commit directly to the main or master branch. Always create a feature branch for changes and submit a pull request.

Committing to feature branches for PRs is fine — no need to ask first or copy commit messages to the clipboard. Always double check with the user before pushing any commits or creating/updating PRs.

## Build and Development Commands

### Building
```bash
# Build using Makefile (recommended for development)
# - Calls build.sh internally to include version info
# - Outputs to build/canasta-{GOOS}-{GOARCH}
# - Creates symlink ./canasta -> build/canasta-{GOOS}-{GOARCH}
make build

# Or build directly with build script
./build.sh

# Cross-compile for specific platform
GOOS=linux GOARCH=amd64 ./build.sh

# Clean build artifacts
make clean
```

Both `make build` and `./build.sh` populate version information via ldflags and output to `build/canasta-{GOOS}-{GOARCH}`. The Makefile additionally creates a symlink `./canasta` for convenience.

### Linting
```bash
# Install golangci-lint (if not already installed via package manager)
make prepare-lint

# Run linter
make lint
```

### Testing
```bash
# Run all tests
go test ./...

# Run tests for specific package
go test ./internal/prompt

# Run with verbose output
go test -v ./...
```

## Architecture

### Command Structure

The CLI uses the Cobra framework with each command in its own package under `cmd/`:
- Entry point: `canasta.go` → `cmd/root/root.go`
- Each command has a `NewCmdCreate()` function that returns a `*cobra.Command`
- Commands are registered in `cmd/root/root.go` init()

### Key Internal Packages

**`internal/orchestrators`** - Docker Compose orchestration
- Executes docker compose commands (up, down, pull, exec)
- Runs commands inside containers via `Exec()` and `ExecWithError()`
- Manages database import/export operations

**`internal/config`** - Installation registry
- Maintains JSON config at `/etc/canasta/conf.json` (when running as root) or `~/.config/canasta/conf.json`
- Tracks all Canasta installations: ID, path, orchestrator
- Functions: `Add()`, `GetDetails()`, `Delete()`, `ListAll()`

**`internal/canasta`** - Core installation logic
- Clones stack repository from https://github.com/CanastaWiki/Canasta-DockerCompose.git
- Manages .env files, LocalSettings.php, wikis.yaml
- Generates and stores passwords (admin passwords per-wiki in config/, DB passwords in .env)
- Handles Caddyfile generation for reverse proxy configuration

**`internal/farmsettings`** - Wiki farm configuration
- Reads/writes `config/wikis.yaml` which defines all wikis in a farm
- Each wiki has: id, url (server name), path (optional)
- Validates wiki IDs

**`internal/mediawiki`** - MediaWiki installation
- Runs MediaWiki's `install.php` via docker exec
- Executes maintenance scripts
- Saves per-wiki admin passwords

**`internal/execute`** - Command execution wrapper
- Runs shell commands with proper error handling and output capture

### Configuration Files

Canasta installations have this structure:
```
{installation-path}/
  .env                           # Environment variables (MW_SITE_SERVER, MW_SITE_FQDN, MYSQL_PASSWORD, WIKI_DB_PASSWORD)
  docker-compose.yml             # From cloned repo
  docker-compose.override.yml    # Optional overrides
  config/
    wikis.yaml                   # Farm configuration: list of wikis
    Caddyfile                    # Generated from wikis.yaml
    admin-password_{wiki-id}     # Generated admin password for each wiki
    settings/
      global/
        README                   # Instructions for global settings
        Vector.php               # Default skin configuration
        CanastaFooterIcon.php    # Canasta footer icon
      wikis/
        {wiki-id}/
          README                 # Instructions for per-wiki settings
```

### Wiki Farm Flow

1. `wikis.yaml` defines all wikis with their IDs and server names
2. During `create`, README files are generated for each wiki's settings directory
3. Caddyfile is rewritten to include all unique server names from wikis.yaml
4. MediaWiki installer runs for each wiki (if not importing a database)

### Database Handling

- Default credentials: user=root, password=mediawiki
- Custom passwords can be set via `--rootdbpass` and `--wikidbpass` flags
- Passwords read from files or prompted if flags provided without files
- Each wiki gets its own MySQL database named after the wiki ID

### Installation Template

Shared installation files (`.env`, `my.cnf`, `config/default.vcl`, Caddyfile templates, default settings, READMEs) are stored in `internal/canasta/installation-template/` and embedded at compile time via `//go:embed all:installation-template`. These files are common to all orchestrators and are copied to the installation directory during `canasta create`.

When adding a new template file:

1. Add the file to the appropriate location under `internal/canasta/installation-template/`
2. If the file is user-editable, add its relative path to `userEditablePaths` in `internal/canasta/canasta.go`
3. Empty directories need a `.gitkeep` file (skipped during copy)

User-editable files use no-clobber semantics (never overwritten if already present). CLI-managed files (e.g., READMEs) are always updated during `canasta upgrade`.

Do not inline template content as string literals in Go code.

## Key Patterns

### Instance Identification
- Installations can be referenced by either:
  - Instance ID (`-i/--id` flag)
  - Installation path (`-p/--path` flag)
- `canasta.CheckCanastaId()` resolves either to a full Installation struct

### Orchestrator Abstraction
- All docker compose operations go through `internal/orchestrators`
- Custom docker compose binary path can be set with `--docker-path` flag
- Stored in config as Orchestrator with id="compose"

### Password Generation
- Uses `sethvargo/go-password` package
- 30 characters, 4 digits, 6 symbols
- Dollar signs replaced with # due to MediaWiki installer bug (T355013)
- Passwords saved to files in installation directory for later retrieval

## Common Development Tasks

### Adding a New Command
1. Create package in `cmd/{command-name}/`
2. Implement `NewCmdCreate() *cobra.Command` function
3. Register in `cmd/root/root.go` init() with `rootCmd.AddCommand()`

### Working with Installations
```go
// Get installation details
instance, err := config.GetDetails(canastaId)

// Execute command in container
output := orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "php maintenance/update.php")

// Check if installation is running
err := orchestrators.CheckRunningStatus(instance)

// Start/Stop/Restart (dev mode is handled automatically based on instance.DevMode)
err := orchestrators.Start(instance)
err := orchestrators.Stop(instance)
err := orchestrators.StopAndStart(instance)
```

### Modifying wikis.yaml
```go
// Read current wikis
ids, serverNames, paths, err := farmsettings.ReadWikisYaml(yamlPath)

// Add new wiki
err := farmsettings.AppendWikiToYaml(yamlPath, wikiId, url)

// Regenerate Caddyfile after changes
err := canasta.RewriteCaddy(installationPath)
```

## Testing Requirements

Any new code added to the CLI must include appropriate tests. If a package does not already have a `_test.go` file, create one. Tests should cover the core logic of the new functionality (argument validation, error paths, expected behavior).

## Important Notes

- Do NOT use sudo when running canasta commands
- Installation IDs must match regex: `^[a-zA-Z0-9]([a-zA-Z0-9-_]*[a-zA-Z0-9])?$`
- Wiki IDs have spaces replaced with underscores and non-alphanumeric characters removed for directory names
- Orchestrator dependencies (docker compose, kubectl) are checked when commands run, not at CLI startup
- Container names follow pattern: {installation-dir-name}_{service}_1 (e.g., "my_canasta_web_1")
