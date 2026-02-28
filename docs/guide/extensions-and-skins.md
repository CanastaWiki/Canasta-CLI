# Extensions and skins

## Contents

- [Overview](#overview)
- [Bundled vs. user extensions](#bundled-vs-user-extensions)
- [Listing available extensions and skins](#listing-available-extensions-and-skins)
- [Enabling extensions and skins](#enabling-extensions-and-skins)
  - [Global vs. per-wiki](#global-vs-per-wiki)
  - [Enabling multiple at once](#enabling-multiple-at-once)
- [Disabling extensions and skins](#disabling-extensions-and-skins)
- [Adding extensions not bundled with Canasta](#adding-extensions-not-bundled-with-canasta)
  - [Overriding a bundled extension](#overriding-a-bundled-extension)
  - [Composer dependencies](#composer-dependencies)
- [Settings file loading order](#settings-file-loading-order)
- [Semantic MediaWiki](#semantic-mediawiki)
- [CirrusSearch / Elasticsearch](#cirrussearch-elasticsearch)
- [How it works under the hood](#how-it-works-under-the-hood)
- [Important notes](#important-notes)

---

## Overview

Canasta bundles over 100 extensions and skins. The CLI provides commands to enable and disable them without manually editing PHP files:

- `canasta extension list` — list available extensions
- `canasta extension enable` — enable one or more extensions
- `canasta extension disable` — disable one or more extensions
- `canasta skin list` — list available skins
- `canasta skin enable` — enable one or more skins
- `canasta skin disable` — disable one or more skins

See the [CLI Reference](../cli/canasta_extension.md) for the full list of flags and usage.

---

## Bundled vs. user extensions

Canasta distinguishes between two types of extensions and skins:

**Bundled extensions/skins** are included in the Canasta Docker image. Inside the container, they live in `canasta-extensions/` and `canasta-skins/`. These are the extensions listed by `canasta extension list` and `canasta skin list`, and are what the `enable`/`disable` commands manage.

**User extensions/skins** are extensions you add yourself. On the host, they go in the `extensions/` and `skins/` directories of your installation. These are not managed by the CLI — you load them by adding `wfLoadExtension()` or `wfLoadSkin()` calls in a settings file under `config/settings/`.

---

## Listing available extensions and skins

To see what bundled extensions are available:

```bash
canasta extension list -i myinstance
```

To see available skins:

```bash
canasta skin list -i myinstance
```

These commands list the names of all bundled extensions or skins shipped with the Canasta image. The installation must be running.

---

## Enabling extensions and skins

```bash
canasta extension enable VisualEditor -i myinstance
canasta skin enable Vector -i myinstance
```

### Global vs. per-wiki

By default, enabling an extension applies to **all wikis** in the installation. To enable an extension for a specific wiki only, use the `-w` flag:

```bash
# Enable for all wikis
canasta extension enable VisualEditor -i myinstance

# Enable for a specific wiki
canasta extension enable SemanticMediaWiki -i myinstance -w docs
```

Disabling works the same way — without `-w` it affects the global setting, with `-w` it affects that wiki only.

### Enabling multiple at once

Pass a comma-separated list of names:

```bash
canasta extension enable VisualEditor,Cite,Scribunto -i myinstance
canasta skin enable Vector,CologneBlue -i myinstance
```

Each extension is processed independently. If one name is invalid, the others are still enabled.

---

## Disabling extensions and skins

```bash
canasta extension disable VisualEditor -i myinstance
canasta skin disable CologneBlue -i myinstance -w docs
```

The CLI can only disable extensions that were enabled using the CLI. If you manually created a settings file to load an extension, the CLI will not remove it.

---

## Adding extensions not bundled with Canasta

To use an extension that is not included in the Canasta image:

1. Place the extension in the `extensions/` directory of your installation (or `skins/` for skins)
2. Create a PHP settings file to load it — either in `config/settings/global/` (for all wikis) or `config/settings/wikis/{wiki-id}/` (for a specific wiki):

```php
<?php
wfLoadExtension( 'MyCustomExtension' );
```

3. Restart the installation:

```bash
canasta restart -i myinstance
```

User extensions are not shown by `canasta extension list` and cannot be managed with `enable`/`disable`. They are loaded by the settings files you create.

### Overriding a bundled extension

If you need a different version of an extension than the one bundled with Canasta, you can override it by placing your version in the `extensions/` (or `skins/`) directory with the same name. This is useful when you need a newer release, a patched fork, or a development branch of a bundled extension.

This works because the host's `extensions/` directory is mounted to `user-extensions/` inside the container, and user extensions are symlinked into the final `extensions/` directory *after* bundled extensions, so they take precedence.

To override a bundled extension:

1. Place your version of the extension in `extensions/{ExtensionName}/`
2. Restart the installation: `canasta restart -i myinstance`
3. The bundled version will be ignored in favor of yours

No changes to settings files are needed — the existing `wfLoadExtension()` call (whether CLI-managed or manual) will load from your copy automatically.

To revert to the bundled version, remove your copy from `extensions/` and restart.

### Composer dependencies

Canasta uses a **unified composer autoloader**. At build time, bundled extensions and skins that need composer dependencies are merged into MediaWiki's root `vendor/autoload.php` via the [composer merge plugin](https://github.com/wikimedia/composer-merge-plugin). This means extensions like SemanticMediaWiki, Maps, and Bootstrap all share a single autoloader with MediaWiki core.

The file `config/composer.local.json` controls which `composer.json` files are merged. The build-time default includes specific entries for bundled extensions that need composer:

```json
{
    "extra": {
        "merge-plugin": {
            "include": [
                "extensions/SemanticMediaWiki/composer.json",
                "extensions/Maps/composer.json",
                "..."
            ]
        }
    }
}
```

If you add a user-extension that has composer dependencies, add its `composer.json` to the include list manually:

```json
{
    "extra": {
        "merge-plugin": {
            "include": [
                "extensions/SemanticMediaWiki/composer.json",
                "extensions/Maps/composer.json",
                "...",
                "user-extensions/MyExtension/composer.json"
            ]
        }
    }
}
```

When the container starts, Canasta detects if `config/composer.local.json` or any referenced `composer.json` has changed since the last run, and re-runs `composer update` if needed.

**To opt out of runtime composer updates**, delete `config/composer.local.json` or empty its `include` array. The build-time autoloader will be used as-is.

---

## Settings file loading order

Each settings directory (`config/settings/global/` and `config/settings/wikis/{wiki-id}/`) can contain two types of files: YAML files and PHP files. Canasta loads them in the following order:

1. **YAML files** (`*.yaml`) — loaded in lexicographic order via MediaWiki's [`SettingsBuilder::loadFile()`](https://www.mediawiki.org/wiki/Manual:YAML_settings_file_format)
2. **PHP files** (`*.php`) — loaded in lexicographic order via `require_once`

Both formats are valid ways to add settings. Configuration variables can be set in either format and will take effect regardless of load order, since extension registration happens after all settings files have been processed. The CLI manages a YAML file called `main.yaml` for extensions and skins enabled via `canasta extension enable` and `canasta skin enable`. You can also create your own YAML files in the same directory. The format supports extensions, skins, and [configuration settings](https://www.mediawiki.org/wiki/Manual:YAML_settings_file_format):

```yaml
extensions:
- MyExtension
skins:
- MySkin
config:
  wgSomeVariable: value
```

PHP files remain useful for settings that require logic, function calls (such as `enableSemantics()`), or conditional expressions that YAML cannot represent.

Global settings are loaded first, followed by per-wiki settings for the current wiki. Within each directory, the YAML-then-PHP order applies.

Since all settings files live in the mounted `config/` volume, they persist across container restarts. Changes take effect on the next page load — no restart is required.

**Note:** YAML settings file loading relies on MediaWiki's `SettingsBuilder` API, which is still marked as unstable and may change in future MediaWiki versions. If the API changes, Canasta will be updated to match. PHP settings files are not affected by this and will always work.

---

## Semantic MediaWiki

[Semantic MediaWiki](https://www.semantic-mediawiki.org/) (SMW) is bundled with Canasta and lets you store and query structured data within your wiki.

### Enabling Semantic MediaWiki

1. Add the following to a settings file (e.g., `config/settings/global/SemanticMediaWiki.php`):

    ```php
    <?php
    wfLoadExtension( 'SemanticMediaWiki' );
    enableSemantics( 'example.org' );
    ```

    Replace `example.org` with your wiki's domain name.

2. Restart Canasta:

    ```bash
    canasta restart -i myinstance
    ```

On the first startup after enabling SMW, Canasta automatically runs `setupStore.php` to initialize the SMW database tables. This creates a `smw.json` configuration file in `config/smw/` on the persistent volume, so the setup only runs once.

### Rebuilding SMW data

After initial setup, you need to run `rebuildData.php` to populate the SMW store. Canasta does not run this automatically because it can take a long time on large wikis. Use the extension maintenance command:

```bash
canasta maintenance extension -i myinstance SemanticMediaWiki rebuildData.php
```

For large wikis, pass options to run in segments:

```bash
canasta maintenance extension -i myinstance SemanticMediaWiki rebuildData.php -s 1000 -e 2000
```

See [Running extension maintenance scripts](general-concepts.md#running-extension-maintenance-scripts) for more examples.

### Notes

- The `enableSemantics()` function is provided by SMW's composer autoloader. Canasta's unified autoloader ensures it is available without any additional steps.
- SMW's `smw.json` is stored persistently in `config/smw/` so it survives container recreations.
- If you need to reinitialize the SMW store (e.g., after a major upgrade), delete `config/smw/smw.json` and restart Canasta.

---

## CirrusSearch / Elasticsearch

[CirrusSearch](https://www.mediawiki.org/wiki/Extension:CirrusSearch) replaces MediaWiki's default search with Elasticsearch. Canasta includes both CirrusSearch and Elasticsearch as built-in services — Elasticsearch runs in a dedicated container alongside the web container.

### Enabling Elasticsearch

Elasticsearch is **not started by default**.

For a new installation, pass an env file with the setting to `canasta create`:

```env
CANASTA_ENABLE_ELASTICSEARCH=true
```

```bash
canasta create -i myinstance -w mywiki -e custom.env
```

For an existing installation:

```bash
canasta config set -i myinstance CANASTA_ENABLE_ELASTICSEARCH=true
```

This starts the Elasticsearch container. For Docker Compose deployments, the CLI automatically syncs the `elasticsearch` profile in `COMPOSE_PROFILES`. For Kubernetes deployments, the CLI includes the Elasticsearch manifest and a `wait-for-elasticsearch` init container in the generated `kustomization.yaml`.

### Enabling CirrusSearch

First, make sure Elasticsearch is enabled (see above). Then enable the CirrusSearch and Elastica extensions:

```bash
canasta extension enable CirrusSearch,Elastica -i myinstance
```

### Rebuilding the search index

After enabling CirrusSearch (or after a major upgrade), you need to build the search index. This is a three-step process:

**Step 1 — Configure index mappings:**

```bash
canasta maintenance extension -i myinstance CirrusSearch UpdateSearchIndexConfig.php --reindexAndRemoveOk --indexIdentifier now
```

**Step 2 — Index page content:**

```bash
canasta maintenance extension -i myinstance CirrusSearch ForceSearchIndex.php --skipLinks --indexOnSkip
```

**Step 3 — Index links:**

```bash
canasta maintenance extension -i myinstance CirrusSearch ForceSearchIndex.php --skipParse
```

All three steps must run in order. On large wikis, the indexing steps (2 and 3) can take a significant amount of time.

### Recreating the index from scratch

To destroy and recreate the index (e.g., after changing analyzers or mappings), use `--startOver` instead of `--reindexAndRemoveOk --indexIdentifier now` in step 1:

```bash
canasta maintenance extension -i myinstance CirrusSearch UpdateSearchIndexConfig.php --startOver
```

Then run steps 2 and 3 as above.

### Wiki farms

In a wiki farm, each wiki has its own search index. Use `--wiki` to target a specific wiki:

```bash
canasta maintenance extension -i myinstance --wiki=docs CirrusSearch UpdateSearchIndexConfig.php --reindexAndRemoveOk --indexIdentifier now
canasta maintenance extension -i myinstance --wiki=docs CirrusSearch ForceSearchIndex.php --skipLinks --indexOnSkip
canasta maintenance extension -i myinstance --wiki=docs CirrusSearch ForceSearchIndex.php --skipParse
```

Or use `--all` to rebuild indexes for every wiki:

```bash
canasta maintenance extension -i myinstance --all CirrusSearch UpdateSearchIndexConfig.php --reindexAndRemoveOk --indexIdentifier now
canasta maintenance extension -i myinstance --all CirrusSearch ForceSearchIndex.php --skipLinks --indexOnSkip
canasta maintenance extension -i myinstance --all CirrusSearch ForceSearchIndex.php --skipParse
```

See [Running extension maintenance scripts](general-concepts.md#running-extension-maintenance-scripts) for general usage of the extension maintenance command.

### Custom Elasticsearch plugins

If you need additional Elasticsearch plugins (for example, `analysis-icu` for ICU-based text analysis), you can build a custom Elasticsearch image using `docker-compose.override.yml` (see [Orchestrators](orchestrators.md#docker-composeoverrideyml) for general override file usage).

**1. Create a Dockerfile for your custom Elasticsearch image:**

Create a file called `Dockerfile.elasticsearch` in a build directory (e.g., `build/`):

```dockerfile
FROM docker.elastic.co/elasticsearch/elasticsearch:7.10.2
RUN elasticsearch-plugin install analysis-icu
```

**2. Create or edit `docker-compose.override.yml`** in your installation directory:

```yaml
services:
  elasticsearch:
    build:
      context: ./build
      dockerfile: Dockerfile.elasticsearch
    image: canasta-elasticsearch-icu:7.10.2
```

**3. Rebuild and restart:**

```bash
docker compose build elasticsearch
canasta restart -i myinstance
```

The override file is automatically picked up by Docker Compose alongside the main `docker-compose.yml`. It is also included in backups.

---

## How it works under the hood

When you run `canasta extension enable VisualEditor`, the CLI adds the extension name to a YAML file called `main.yaml` on the host filesystem:

- **Global enable** updates `config/settings/global/main.yaml`:
  ```yaml
  extensions:
  - VisualEditor
  ```

- **Per-wiki enable** (`-w docs`) updates `config/settings/wikis/docs/main.yaml` with the same format.

Extensions and skins are sorted alphabetically within the file. Disabling removes the entry. If the file becomes empty, it is deleted. The CLI never modifies any other files in the settings directory.

For details on how these files are processed at runtime, see [Settings file loading order](#settings-file-loading-order).

---

## Important notes

- **Extension and skin names are case-sensitive.** Use the exact name as shown by `canasta extension list` or `canasta skin list`.
- **`enable` and `list` require running containers.** The `enable` command checks that the extension exists in the container image before adding it to the config. The `list` command queries the container.
- **`disable` does not require running containers.** It operates on host-side YAML files only.
- **Enabling is idempotent.** Running `enable` on an already-enabled extension is a no-op.
- **Disable only works on CLI-managed extensions.** Only entries in `main.yaml` are affected; other files are left alone.
- **No restart required.** Changes take effect on the next page load since PHP reads the settings files on each request.
