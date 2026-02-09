# Troubleshooting

This page covers common troubleshooting steps for Canasta installations.

## Contents

- [Checking container status](#checking-container-status)
- [Viewing container logs](#viewing-container-logs)
  - [Log file locations](#log-file-locations)
  - [Enabling the MediaWiki debug log](#enabling-the-mediawiki-debug-log)
- [Accessing the database](#accessing-the-database)
- [Running commands inside containers](#running-commands-inside-containers)
- [Common issues](#common-issues)

---

## Checking container status

To see if your Canasta containers are running:
```bash
cd /path/to/installation
docker compose ps
```

---

## Viewing container logs

To view logs from the web container:
```bash
cd /path/to/installation
docker compose logs web
```

To follow logs in real-time:
```bash
docker compose logs -f web
```

To view logs from all containers:
```bash
docker compose logs
```

### Log file locations

For more detailed debugging, you can access log files inside the container:

| Log | Description | Command |
|-----|-------------|---------|
| MediaWiki debug | Application debug log (see below) | `docker compose exec web tail -f /var/log/mediawiki/debug.log` |
| Apache error | PHP errors and Apache warnings | `docker compose exec web tail -f /var/log/apache2/error_log.current` |
| Apache access | HTTP request log | `docker compose exec web tail -f /var/log/apache2/access_log.current` |

### Enabling the MediaWiki debug log

The MediaWiki debug log is the most useful log for debugging application issues, but it requires explicit configuration. Add this to a settings file (e.g., `config/settings/global/Debug.php`):

```php
<?php
$wgDebugLogFile = '/var/log/mediawiki/debug.log';
```

Then restart the container:

```bash
canasta restart -i myinstance
```

---

## Accessing the database

To connect to the MySQL database directly:
```bash
cd /path/to/installation
docker compose exec db mysql -u root -p
```
Enter the root database password from your `.env` file when prompted.

---

## Running commands inside containers

To run arbitrary commands inside the web container:
```bash
cd /path/to/installation
docker compose exec web <command>
```

For example, to check PHP version:
```bash
docker compose exec web php -v
```

To get a shell inside the container:
```bash
docker compose exec web bash
```

---

## Common issues

**Installation fails with "Canasta installation with the ID already exists"**
- An installation with that ID is already registered. Use `canasta list` to see existing installations, or choose a different ID.

**Cannot connect to Docker**
- Ensure Docker is running: `systemctl status docker`
- Ensure your user has Docker access (on Linux: `sudo usermod -aG docker $USER`, then log out and back in)

**Wiki not accessible after creation**
- Check that containers are running: `docker compose ps`
- Verify the domain/URL configuration in `config/wikis.yaml`
- Check container logs for errors: `docker compose logs web`

**Wiki not accessible on non-standard ports**
- When using non-standard ports, the port must be included in the URL you use to access the wiki in your browser (e.g., `https://localhost:8443`, not `https://localhost`)
- The port must also appear in the URL in `config/wikis.yaml` (e.g., `localhost:8443` or `localhost:8443/wiki2`)
- This applies to both path-based and subdomain-based wikis

**SSL/TLS errors when running behind a reverse proxy or load balancer**
- If your server is already behind something that terminates SSL (e.g. Cloudflare, nginx, a cloud load balancer), Caddy's automatic HTTPS will conflict with it. Add the following to `config/Caddyfile.custom` to disable it:
  ```
  auto_https off
  ```

**Permission denied errors**
- Ensure your user has Docker access (see above)
- Ensure the installation directory has proper ownership
