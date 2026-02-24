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
- [Architecture notes](#architecture-notes)
- [Security notes](#security-notes)

---

## Enabling observability

To enable the observability stack, set `CANASTA_ENABLE_OBSERVABILITY=true` in your `.env` file. This works with both Docker Compose and Kubernetes orchestrators.

### New installations

Create an `.env` file with observability enabled, then pass it to `canasta create`:

```bash
echo "CANASTA_ENABLE_OBSERVABILITY=true" > my.env
canasta create -i myinstance -w mywiki -a admin -e my.env
```

### Existing installations

Add `CANASTA_ENABLE_OBSERVABILITY=true` to the `.env` file in your installation directory, then upgrade:

```bash
canasta upgrade
```

In both cases, the CLI automatically:
- Generates `OS_USER`, `OS_PASSWORD`, and `OS_PASSWORD_HASH` in `.env`
- Adds the OpenSearch Dashboards reverse proxy block to the Caddyfile
- For Docker Compose: syncs `COMPOSE_PROFILES` to include `observable`
- For Kubernetes: adds OpenSearch, Dashboards, and Fluent Bit resources to the kustomization

> **Note:** If you are migrating from an older Canasta installation that used `COMPOSE_PROFILES=observable`, running `canasta upgrade` will automatically add `CANASTA_ENABLE_OBSERVABILITY=true` to your `.env` file.

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

Index patterns are created automatically by an init container (Docker Compose) or init job (Kubernetes) when the observability stack starts.

If automatic creation fails, you can create patterns manually:

1. Open **OpenSearch Dashboards** > **Stack Management** > **Index Patterns**.
2. Create patterns for the indices that exist:
   - `caddy-logs-*`
   - `mysql-logs-*`
   - `mediawiki-logs-*` (only if MediaWiki logging is enabled)
3. Select `@timestamp` as the time field.

### Checking init status

- **Docker Compose:** `docker logs <id>-observable-init-1`
- **Kubernetes:** `kubectl logs job/observable-init -n <namespace>`

---

## Verifying logs are flowing

If you do not see logs in Dashboards:

- Generate some activity (browse the wiki, log in, edit a page).
- Ensure the observability containers are running: `canasta list` or check your orchestrator.
- Check container logs for errors:
  - **Docker Compose:**
    ```bash
    docker logs <id>-logstash-1
    docker logs <id>-opensearch-1
    ```
  - **Kubernetes:**
    ```bash
    kubectl logs deployment/opensearch -n <namespace>
    kubectl logs deployment/fluent-bit -n <namespace>
    ```

---

## Architecture notes

The observability stack uses different log shipping approaches depending on the orchestrator:

- **Docker Compose:** Uses Logstash to read log files from shared Docker volumes and ship them to OpenSearch.
- **Kubernetes:** Uses a standalone Fluent Bit Deployment that reads log files from PVC volumes shared with the web, caddy, and db pods.

Both approaches ship logs to the same OpenSearch indices and Dashboards is accessible at `/opensearch` on both orchestrators.

---

## Security notes

- OpenSearch has its security plugin disabled (`plugins.security.disabled=true`). Access to OpenSearch Dashboards is protected by Caddy's basicauth. OpenSearch itself is only accessible within the container network (no ports exposed to the host).
- OpenSearch Dashboards port 5601 is not exposed to the host; access is exclusively through the Caddy reverse proxy at `/opensearch`.
