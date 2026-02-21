# Observability (OpenSearch + Logstash + Dashboards)

Canasta includes an optional observability stack that collects logs from MediaWiki, Caddy, and MySQL and makes them searchable through OpenSearch Dashboards.

## Contents

- [Enabling observability](#enabling-observability)
  - [New installations](#new-installations)
  - [Existing installations](#existing-installations)
- [Accessing OpenSearch Dashboards](#accessing-opensearch-dashboards)
- [Viewing logs](#viewing-logs)
- [Enabling MediaWiki logging](#enabling-mediawiki-logging)
- [Index patterns](#index-patterns)
- [Verifying logs are flowing](#verifying-logs-are-flowing)
- [Security notes](#security-notes)

---

## Enabling observability

The observability stack runs as an optional Docker Compose profile. To enable it, set `COMPOSE_PROFILES` to include `observable` in your `.env` file.

### New installations

Create an `.env` file with the profile enabled, then pass it to `canasta create`:

```bash
echo "COMPOSE_PROFILES=observable" > my.env
canasta create compose -i myinstance -w mywiki -a admin -e my.env
```

Or append `observable` to an existing `.env` file that already sets `COMPOSE_PROFILES`:

```
COMPOSE_PROFILES=web,observable
```

### Existing installations

Add `COMPOSE_PROFILES=observable` (or append `,observable`) to the `.env` file in your installation directory, then upgrade:

```bash
canasta upgrade -i myinstance
```

In both cases, the CLI automatically:
- Generates `OS_USER`, `OS_PASSWORD`, and `OS_PASSWORD_HASH` in `.env`
- Adds the OpenSearch Dashboards reverse proxy block to the Caddyfile

---

## Accessing OpenSearch Dashboards

- **URL:** `https://<your-domain>/opensearch`
- **Login:** Use the `OS_USER` and `OS_PASSWORD` values from your installation's `.env` file.

---

## Viewing logs

1. Open OpenSearch Dashboards at `/opensearch`.
2. Go to **Discover**.
3. Select an index pattern from the top-left dropdown.
4. Adjust the time range (top-right) to include recent activity.

By default, two log sources are available immediately:

| Index pattern | Source |
|---------------|--------|
| `caddy-logs-*` | Caddy reverse proxy access logs |
| `mysql-logs-*` | MySQL error logs |

The `mediawiki-logs-*` index pattern only appears if MediaWiki logging is enabled (see below).

---

## Enabling MediaWiki logging

By default, MediaWiki does not write log files. To enable logging, add the following to `config/settings/global/Logging.php` (or a per-wiki settings file):

```php
<?php
$wgDebugLogFile = "/var/log/mediawiki/debug.log";
$wgDBerrorLog = "/var/log/mediawiki/dberror.log";
$wgDebugLogGroups = [
    'exception' => "/var/log/mediawiki/exception.log",
    'error' => "/var/log/mediawiki/error.log",
];
```

Then restart:

```bash
canasta restart -i myinstance
```

Once log files are created, the `mediawiki-logs-*` index pattern will appear automatically in Dashboards.

---

## Index patterns

Index patterns are created automatically by an init container when the observability profile starts. It waits for OpenSearch Dashboards to be ready and for at least one log index to exist, then creates patterns for each available index. A second pass runs 60 seconds later to pick up any late-arriving indices.

If automatic creation fails (check `docker logs <id>-observable-init-1`), you can create patterns manually:

1. Open **OpenSearch Dashboards** > **Stack Management** > **Index Patterns**.
2. Create patterns for the indices that exist:
   - `caddy-logs-*`
   - `mysql-logs-*`
   - `mediawiki-logs-*` (only if MediaWiki logging is enabled)
3. Select `@timestamp` as the time field.

---

## Verifying logs are flowing

If you do not see logs in Dashboards:

- Generate some activity (browse the wiki, log in, edit a page).
- Ensure the observability containers are running: `canasta list` or `docker ps`.
- Check container logs for errors:
  ```bash
  docker logs <id>-logstash-1
  docker logs <id>-opensearch-1
  ```

---

## Security notes

- OpenSearch has its security plugin disabled (`plugins.security.disabled=true`). Access to OpenSearch Dashboards is protected by Caddy's basicauth. OpenSearch itself is only accessible within the Docker network (no ports exposed to the host).
- OpenSearch Dashboards port 5601 is not exposed to the host; access is exclusively through the Caddy reverse proxy at `/opensearch`.
