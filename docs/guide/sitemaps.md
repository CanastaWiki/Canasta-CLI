# Sitemaps

XML sitemaps help search engines discover and index pages on your wiki. Canasta provides commands to generate and remove sitemaps, and a background process to keep them up to date.

## Contents

- [Why sitemaps matter](#why-sitemaps-matter)
- [Generating sitemaps](#generating-sitemaps)
  - [Redirect pages](#redirect-pages)
  - [Filtering namespaces](#filtering-namespaces)
- [Removing sitemaps](#removing-sitemaps)
- [Background refresh](#background-refresh)
- [File storage](#file-storage)
- [robots.txt integration](#robotstxt-integration)
- [Verifying sitemaps](#verifying-sitemaps)
- [Troubleshooting](#troubleshooting)

---

## Why sitemaps matter

Search engines like Google and Bing use [XML sitemaps](https://www.sitemaps.org/) to discover pages on your site. Without a sitemap, crawlers rely on following links from your main page, which can miss pages that are poorly linked or deeply nested. Providing a sitemap ensures that all your wiki's content is discoverable.

---

## Generating sitemaps

Use `canasta sitemap generate` to create sitemaps for your wikis.

Generate a sitemap for a specific wiki:

```bash
canasta sitemap generate -i myinstance -w mywiki
```

Generate sitemaps for all wikis in the installation:

```bash
canasta sitemap generate -i myinstance
```

Once generated, a [background process](#background-refresh) automatically keeps the sitemaps up to date.

### Redirect pages

Redirect pages are automatically excluded from sitemaps (via MediaWiki's `--skip-redirects` flag). Since redirect pages point to other pages that are already in the sitemap, including them would add no value for search engines and unnecessarily increase the sitemap size.

### Filtering namespaces

You can control which namespaces are included in sitemaps by setting [`$wgSitemapNamespaces`](https://www.mediawiki.org/wiki/Manual:$wgSitemapNamespaces) in your wiki's Settings.php. For example, to include only the main namespace and the Help namespace:

```php
$wgSitemapNamespaces = [ NS_MAIN, NS_HELP ];
```

If `$wgSitemapNamespaces` is not set, MediaWiki includes all namespaces by default.

---

## Removing sitemaps

Use `canasta sitemap remove` to delete sitemap files. Once removed, the background generator will skip those wikis until sitemaps are generated again.

Remove the sitemap for a specific wiki:

```bash
canasta sitemap remove -i myinstance -w mywiki
```

Remove sitemaps for all wikis (prompts for confirmation):

```bash
canasta sitemap remove -i myinstance
```

To skip the confirmation prompt, use `-y`:

```bash
canasta sitemap remove -i myinstance -y
```

---

## Background refresh

After sitemaps are generated for a wiki, a background process inside the container automatically refreshes them on a regular schedule. The refresh interval is controlled by the `MW_SITEMAP_PAUSE_DAYS` environment variable, which defaults to `1` (day). Values less than 1 are treated as 1.

The background generator runs continuously inside the container — it starts after a 30-second delay and loops indefinitely, sleeping for the configured interval between runs. It only refreshes sitemaps for wikis that already have sitemap files — generating sitemaps for a wiki opts it in, and removing them opts it out.

The generator writes daily-rotating log files inside the container at `/var/log/mediawiki/mwsitemapgen_log_YYYYMMDD`.

---

## File storage

Sitemap files are stored in the `public_assets/<wiki-id>/sitemap/` directory within your installation. They are served at the URL path `/public_assets/sitemap/`.

```
myinstance/
  public_assets/
    mywiki/
      sitemap/
        sitemap-mywiki-NS_0-0.xml.gz
        sitemap-mywiki-NS_6-0.xml.gz
        sitemap-index-mywiki.xml
```

The file(s) are compressed (`.xml.gz`) for efficiency, with an index file that lists all the individual sitemap files.

---

## robots.txt integration

Canasta dynamically generates `robots.txt` for each wiki. When sitemap files exist for a wiki, the sitemap index URL is automatically included in the response:

```
Sitemap: https://example.com/public_assets/sitemap/sitemap-index-mywiki.xml
```

No configuration is needed — the presence of sitemap files is the sole signal. When sitemaps are removed, the `robots.txt` entry is removed as well.

### Disabling robots

To tell all search engines not to crawl any of your wikis, set `ROBOTS_DISALLOWED=true` in your `.env` file. This causes `robots.txt` to return `Disallow: /` for all user agents.

---

## Verifying sitemaps

After generating sitemaps, verify they are accessible:

1. Check the sitemap index URL in your browser:
   ```
   https://example.com/public_assets/sitemap/sitemap-index-mywiki.xml
   ```

2. Verify `robots.txt` includes the sitemap URL:
   ```
   https://example.com/robots.txt
   ```

3. Submit the sitemap URL to search engine webmaster tools for faster indexing:
   - [Google Search Console](https://search.google.com/search-console)
   - [Bing Webmaster Tools](https://www.bing.com/webmasters)

---

## Troubleshooting

### Sitemaps not updating

- Verify the containers are running: `canasta start -i myinstance`
- Check the `MW_SITEMAP_PAUSE_DAYS` value in your `.env` file — a high value means less frequent refreshes
- Check the generator log inside the container for errors: `/var/log/mediawiki/mwsitemapgen_log_YYYYMMDD`
- Regenerate manually: `canasta sitemap generate -i myinstance -w mywiki`

### Search engines not finding sitemaps

- Confirm the sitemap URL is present in `robots.txt` (visit `https://yourwiki/robots.txt`)
- If sitemap files exist but `robots.txt` does not list them, restart the installation: `canasta restart -i myinstance`
- Submit the sitemap URL directly in your search engine's webmaster tools

### Permission errors

- Ensure your user is in the `www-data` group (see [Installation guide](../installation.md#linux))
- The sitemap directory must be writable by the `www-data` user inside the container — `canasta sitemap generate` handles this automatically
