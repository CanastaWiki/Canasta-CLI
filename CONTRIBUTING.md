# Contributing to Canasta-Ansible

This document covers the local development workflow for the Canasta-Ansible repository. For ecosystem-wide contributing guidance (CanastaBase, the Canasta image, building a custom Canasta-like distribution), see the [Contributing guide on canasta.wiki](https://canasta.wiki/wiki/Help:Contributing).

## Setting up a development environment

```bash
git clone https://github.com/CanastaWiki/Canasta-Ansible.git
cd Canasta-Ansible

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml
```

Run `make help` to see the available targets.

## Tests, lint, and validation

```bash
make test-unit            # Unit tests
make test-integration     # Integration tests (requires Docker)
make lint                 # ansible-lint + yamllint
make validate             # Validate command_definitions.yml structure
make docs                 # Regenerate docs/commands/*.md
```

## Architecture

```
canasta wrapper → canasta.py (argparse) → ansible-playbook canasta.yml → roles/<role>/tasks/<action>.yml
```

`meta/command_definitions.yml` is the single source of truth for all commands, parameters, and documentation. Both the CLI's argparse subcommands and the auto-generated `docs/commands/*.md` files are derived from it.

Roles (`roles/`):

- `common` — shared modules and library (registry, env, wikis.yaml, etc.)
- `orchestrator` — Compose / Kubernetes dispatch
- `create`, `delete`, `instance_lifecycle` — instance lifecycle
- `config`, `extensions_skins`, `maintenance`, `mediawiki` — instance contents
- `backup`, `gitops`, `devmode`, `sitemap`, `upgrade` — operations
- `imagebuild` — local image builds (`--build-from`)

Custom Python modules live in `roles/common/library/` and `roles/extensions_skins/library/`:

- `canasta_registry` — read/write `conf.json`
- `canasta_env` — read/write `.env` files (preserves comments and ordering)
- `canasta_wikis_yaml` — read/write `wikis.yaml`
- `canasta_settings_yaml` — read/write `settings.yaml`
- `canasta_farmsettings` — wiki-farm setting helpers

## Wiki publishing

CLI reference pages on canasta.wiki are auto-generated from `meta/command_definitions.yml`:

```bash
make docs                                       # local Markdown in docs/commands/
python scripts/wiki_publish.py --dry-run        # preview wikitext output
python scripts/wiki_publish.py \                # publish to a wiki
  --api https://canasta.wiki/w/api.php \
  --user User@BotName --pass botpassword
```

The publisher writes pages under the `CLI:` namespace plus `MediaWiki:Menu-cli-reference` for the sidebar. Pages in the `Help:` namespace are hand-curated and not managed by this script.

## Pull requests

- Every PR should reference at least one GitHub issue.
- Use feature branches (never push directly to `main`).
- Run `make test-unit && make validate` before opening a PR.
- Follow the existing commit-message style (`git log` for examples).

## Design documents

Longer-form design docs live in `docs/`:

- [`docs/multi-node.md`](docs/multi-node.md) — multi-node Kubernetes deployment walkthrough.
- [`docs/k8s-multi-env-gitops-design.md`](docs/k8s-multi-env-gitops-design.md) — Argo CD / dev → staging → prod GitOps design notes.
