# Contributing

This guide covers the release workflow for the Canasta project and how the various repositories work together.

## Contents

- [Repository overview](#repository-overview)
- [Release workflow](#release-workflow)
- [Image tagging strategy](#image-tagging-strategy)
- [Updating the CLI default image](#updating-the-cli-default-image)
- [Testing changes locally](#testing-changes-locally)

---

## Repository overview

The Canasta project spans several repositories:

| Repository | Description |
|-----------|-------------|
| [CanastaBase](https://github.com/CanastaWiki/CanastaBase) | Base Docker image with MediaWiki, Apache, PHP, bundled extensions/skins, and maintenance scripts |
| [Canasta](https://github.com/CanastaWiki/Canasta) | Thin layer on top of CanastaBase that adds additional extensions/skins, patches, and extension-specific maintenance scripts |
| [Canasta-CLI](https://github.com/CanastaWiki/Canasta-CLI) | Go CLI tool for managing Canasta installations |

The Docker image hierarchy is:

```
CanastaBase (mediawiki + core extensions + skins + maintenance scripts)
  └── Canasta (additional extensions/skins, patches, extension-specific maintenance)
```

Caddy, Varnish, MariaDB, and Elasticsearch/OpenSearch run as **separate containers** alongside the Canasta web container — they are not part of the Canasta image.

---

## Release workflow

A typical Canasta release involves changes across one or more repositories. The general flow is:

### 1. Merge changes into CanastaBase and/or Canasta

Submit and merge pull requests to the relevant repos. For changes that span both CanastaBase and Canasta (e.g., a new MediaWiki version), merge CanastaBase first so the new base image is available when Canasta builds.

### 2. CI builds on every master push

Every push to `master` triggers the CI workflow, which:

1. Builds the Docker image for `linux/amd64` and `linux/arm64`
2. Pushes the mutable rolling tags: `latest`, `<MW_MAJOR>-latest`, `<MW_VERSION>-latest`, and `<MW_VERSION>-<DATE>-<SHA>`
3. Runs the auto-tag job: if the version in the `VERSION` file does not already have a corresponding Git tag, it creates one (e.g., `v3.3.0`)

### 3. Bump the VERSION file for a new release

Once all PRs for the release are merged into `master`, update the `VERSION` file in the Canasta repository to the new version number (e.g., `3.3.0`). This can be done directly on `master` or via a final PR.

The next CI run will see that the `v3.3.0` tag does not exist and create it. The new tag triggers a second CI run that builds and pushes an **immutable** versioned image tag (e.g., `3.3.0`). This is the tag that CLI installations are pinned to — it is never overwritten by future builds.

### 4. Update the CLI default image tag

After the versioned image is published, update the `CANASTA_VERSION` file in the Canasta-CLI repository to match the new version (e.g., `3.3.0`). The build script reads this file and sets `DefaultImageTag` via ldflags.

This ensures new installations created with `canasta create` use the new version. Existing installations keep whatever version is in their `.env` file until explicitly upgraded.

### 5. Release the CLI

Bump the `VERSION` file in the Canasta-CLI repository to trigger a new release. The `VERSION` file controls the CLI version number, which is independent of the Canasta image version in `CANASTA_VERSION`. You can release new CLI versions without bumping the Canasta image version, and vice versa.

---

## Image tagging strategy

The Canasta Docker image uses several tag types:

| Tag | When published | Mutable? | Example |
|-----|---------------|----------|---------|
| `latest` | Every push to `master` | Yes | `canasta:latest` |
| `<MW_MAJOR>-latest` | Every push to `master` | Yes | `canasta:1.43-latest` |
| `<MW_VERSION>-latest` | Every push to `master` | Yes | `canasta:1.43.1-latest` |
| `<MW_VERSION>-<DATE>-<SHA>` | Every push to `master` | No | `canasta:1.43.1-20260224-abc1234` |
| `<CANASTA_VERSION>` | Git tag push (auto or manual) | No | `canasta:3.2.0` |
| `<MW_VERSION>-<DATE>-<PR>` | Pull requests | No | `canasta:1.43.1-20260224-42` |

The CLI pins installations to the immutable `<CANASTA_VERSION>` tag (e.g., `3.2.0`). This guarantees reproducible deployments — the image an installation uses does not change unless the user explicitly upgrades.

---

## Updating the CLI default image

The default image tag is controlled by the `CANASTA_VERSION` file at the repository root. The build script reads this file and injects the value into `DefaultImageTag` via ldflags.

When `canasta create` runs, it writes `CANASTA_IMAGE=ghcr.io/canastawiki/canasta:<DefaultImageTag>` to the installation's `.env` file. The `docker-compose.yml` and Kubernetes `web.yaml` templates reference `${CANASTA_IMAGE:-ghcr.io/canastawiki/canasta:latest}` — the `latest` fallback is never used in practice since `CANASTA_IMAGE` is always set.

To update:

1. Change `CANASTA_VERSION` to the new image version
2. Submit a PR to `Canasta-CLI`
3. After merging, bump `VERSION` if you want a new CLI release

## Version files

The CLI uses two version files:

| File | Controls | Example |
|------|----------|---------|
| `VERSION` | CLI version number (triggers release when changed) | `3.6.0` |
| `CANASTA_VERSION` | Default Canasta Docker image tag | `3.5.1` |

These are independent — you can release CLI updates (bug fixes, new commands) without bumping the Canasta image version, and update the pinned Canasta version without changing the CLI version.

---

## Testing changes locally

To test changes to CanastaBase or Canasta before they are published, use `--build-from`:

```bash
# Directory structure expected by --build-from:
# ~/canasta-repos/
#   ├── Canasta/       (required)
#   └── CanastaBase/   (optional — if present, built first)

canasta create -i test-instance -w mywiki -n localhost \
  --build-from ~/canasta-repos
```

This builds the image(s) locally and uses them for the installation. See [Custom Canasta images](general-concepts.md#custom-canasta-images) and [Development mode](devmode.md#building-from-local-source) for details.
