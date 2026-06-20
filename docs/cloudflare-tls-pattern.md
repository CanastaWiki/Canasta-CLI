# Cloudflare TLS Termination Pattern

## Problem

Canasta defaults to `CADDY_AUTO_HTTPS=on`, which generates a Caddyfile with
automatic HTTPS (Let's Encrypt) and HTTP-to-HTTPS redirects (301/308). When
Cloudflare terminates TLS at the edge — a common and recommended architecture
for production MediaWiki deployments — this default causes two problems:

1. **Unnecessary Let's Encrypt certificate attempts** — Caddy tries to
   provision certificates even though TLS is handled by Cloudflare.
2. **Double-redirect loops** — Cloudflare forwards client HTTPS requests as
   plain HTTP to the origin (port 80), but Caddy then redirects back to HTTPS
   (308), creating a loop:
   ```
   Client → HTTPS → Cloudflare → HTTP:80 → Caddy → 308 HTTPS → ✗
   ```

## Environment

- Cloudflare proxy (orange cloud) enabled for your domain
- Canasta with Caddy reverse proxy (Compose orchestrator)
- Architecture: `Visitor → HTTPS → Cloudflare → HTTP:80 → EC2 → Caddy`

## Solution

Canasta already supports this architecture through existing configuration.
No code changes are needed — just two configuration steps.

### Step 1: Set `CADDY_AUTO_HTTPS=off`

Pass this in your env file (`-e`) when running `canasta create`, or set it
on an existing instance with `canasta config set`:

```ini
CADDY_AUTO_HTTPS=off
```

This tells Canasta to generate a Caddyfile with `http://` site prefixes
instead of `https://`, preventing Caddy from attempting auto-TLS.

### Step 2: (Optional) Custom Caddyfile for additional headers

The generated Caddyfile handles the basic reverse proxy. For production
use, add security headers and rate limiting via `Caddyfile.site`, which
is imported automatically inside the site block:

```caddy
# Security headers
header {
    X-Frame-Options           "SAMEORIGIN"
    X-Content-Type-Options    "nosniff"
}

# Rate limiting
rate_limit {
    zone general {
        key {remote_host}
        events 10
        window 1s
    }
}
```

## Security Considerations

When TLS is terminated at the Cloudflare edge, the origin server (your
Canasta instance) receives plain HTTP on port 80. To maintain security:

- **Restrict port 80 to Cloudflare IPs only** — configure your firewall or
  security group to allow inbound HTTP traffic only from
  [Cloudflare's IP ranges](https://www.cloudflare.com/ips-v4). This prevents
  direct-origin HTTP access.
- **Cloudflare SSL/TLS setting** — in the Cloudflare Dashboard for your
  domain, set SSL/TLS to **Full** or **Flexible** depending on whether you
  want encrypted traffic between Cloudflare and your origin:
  - **Full (recommended)** — traffic between Cloudflare and origin is
    encrypted using a self-signed or custom certificate on the origin.
    Requires port 443 or alternative HTTPS port to be open on the origin.
  - **Flexible** — traffic between Cloudflare and origin is plain HTTP.
    Only port 80 is needed on the origin. Note that this means traffic
    within your cloud provider's network is unencrypted.
- **Caddy still handles TLS for non-proxied subdomains** — if you have
  subdomains that bypass Cloudflare (grey cloud), they will still need
  Caddy auto-TLS. Either proxy them through Cloudflare as well, or
  configure separate handling in `Caddyfile.global`.

## How It Works

When `CADDY_AUTO_HTTPS=off` is set, the Canasta CLI:

1. Generates the Caddyfile with `http://` site addresses instead of `https://`
2. Does not configure any TLS certificates in Caddy
3. Does not emit HTTP-to-HTTPS redirect rules

Caddy listens on port 80 only and proxies requests to the web container
as plain HTTP. All TLS concerns are delegated to Cloudflare.

## Verification

After configuring `CADDY_AUTO_HTTPS=off`, verify that Caddy is not
redirecting:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost/
# Should return 200, not 301 or 308

curl -s -o /dev/null -w "%{http_code}" https://your-domain.example.com/
# Should return 200 (via Cloudflare)
```

Check that Caddy has no TLS certificates:

```bash
docker compose exec caddy caddy cert-info 2>&1 | head -5
# Should indicate no certificates or an empty store
```

## Related

- Requires `CADDY_AUTO_HTTPS` env var support (available since Canasta CLI v4.x)
- See `canasta config set` for modifying existing instances
