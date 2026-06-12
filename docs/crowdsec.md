# CrowdSec (Compose)

[CrowdSec](https://www.crowdsec.net/) is an open-source, behavior-based
threat detection engine. It parses the Caddy access log, correlates
attack patterns (brute force, credential stuffing, scanning, aggressive
crawlers), and publishes IP-level decisions to a *bouncer* — here, a
native Caddy module — that blocks malicious traffic at the edge.

CrowdSec is **opt-in and off by default**. With
`CANASTA_ENABLE_CROWDSEC=false` (the default) nothing changes: the caddy
service runs the stock upstream image and no CrowdSec container is
started. Enabling the feature is safe even before a bouncer is enrolled —
Caddy starts normally and simply enforces nothing until you provide a
key.

> **Scope:** this covers the Docker Compose orchestrator only. CrowdSec
> integrates at the ingress layer, and the Kubernetes path (Traefik
> middleware vs. in-cluster Caddy) is tracked as a separate follow-up.

## What enabling it does

1. Adds the `crowdsec` Compose profile, starting a
   `crowdsecurity/crowdsec` container (LAPI on `:8080`) that reads the
   Caddy access log via `config/crowdsec/acquis.yaml`.
2. Switches the caddy service to the plugin image
   `ghcr.io/canastawiki/canasta-caddy` (upstream Caddy plus the bouncer
   and `caddy-cdn-ranges` plugins) by setting the managed
   `CANASTA_CADDY_IMAGE` value in `.env`. (A custom `CANASTA_CADDY_IMAGE`
   you set yourself is left untouched — see *Custom Caddy image* below.)
3. Injects the bouncer directives into the generated Caddyfile **only
   once a bouncer API key is present**. Without a key the directive is
   omitted, so Caddy boots cleanly in a degraded "blocks nothing" state.

## Enable it

```bash
canasta config set CANASTA_ENABLE_CROWDSEC=true -i mysite
```

At this point CrowdSec is running and learning, but the bouncer is not
yet enforcing decisions (no key). Enroll one:

```bash
canasta crowdsec enroll -i mysite
```

`enroll` registers the Caddy bouncer with the running engine, captures
the generated API key, stores it as `CROWDSEC_BOUNCER_API_KEY`, and
restarts so the Caddyfile picks up the `crowdsec` directive and the
bouncer begins enforcing decisions. It is idempotent — running it again
is a no-op once a key is stored; use `--force` to revoke the bouncer and
issue a fresh key.

Check what's registered and what's currently blocked:

```bash
canasta crowdsec status -i mysite
```

## Disable it

```bash
canasta config set CANASTA_ENABLE_CROWDSEC=false -i mysite
```

This drops the `crowdsec` profile, reverts `CANASTA_CADDY_IMAGE` to the
stock Caddy image, and removes the bouncer directive from the Caddyfile.
`CROWDSEC_BOUNCER_API_KEY` is left in `.env` (harmless when the feature
is off) so re-enabling later does not require re-enrolling.

## How the "optional key" works

The original community implementation required
`CROWDSEC_BOUNCER_API_KEY` unconditionally: if it was unset, Caddy failed
to parse its config and every deployment broke. That is why it was
reverted.

Here, `rewrite_caddy.yml` treats CrowdSec as *active* only when **both**
`CANASTA_ENABLE_CROWDSEC=true` **and** a non-empty
`CROWDSEC_BOUNCER_API_KEY` are set. The bouncer directive — and the
`{env.CROWDSEC_BOUNCER_API_KEY}` placeholder it depends on — is rendered
only in that case. An enabled-but-keyless instance therefore produces a
Caddyfile with no CrowdSec directive at all, and Caddy starts normally.

## Custom Caddy image

`CANASTA_CADDY_IMAGE` is managed automatically — it switches to
`ghcr.io/canastawiki/canasta-caddy` whenever a plugin feature is on
(CrowdSec, or a `cloudflare`/`imperva` trusted-proxy mode) — but only
when it is empty or already holds the managed image. If you set it to
your own value (e.g. a Caddy build with extra plugins), the toggle won't
overwrite it. In that case you're responsible for including the
[`caddy-crowdsec-bouncer`](https://github.com/hslatman/caddy-crowdsec-bouncer)
HTTP module and
[`caddy-cdn-ranges`](https://github.com/sarumaj/caddy-cdn-ranges) for the
features you use, or the rendered directives will fail to load.

## Running behind a CDN or WAF (Cloudflare, Imperva)

CrowdSec only works if it sees the **real client IP**. CrowdSec parses
Caddy's access log and the bouncer enforces in Caddy, so as long as Caddy
is the true edge (publishes :80/:443 directly), it already records the
real client — the Varnish/Apache hops are downstream and invisible to it.

But if Caddy itself sits behind a CDN/WAF (Cloudflare, Imperva, an LB),
that proxy terminates the client connection and opens its own connection
to Caddy. Without configuration, Caddy would see the *proxy's* IP — so
CrowdSec would detect, and the bouncer would block, your CDN instead of
the attacker. Tell Caddy who to trust:

```bash
canasta config set CADDY_TRUSTED_PROXIES=cloudflare -i mysite   # behind Cloudflare
canasta config set CADDY_TRUSTED_PROXIES=imperva    -i mysite   # behind Imperva
```

Each provider mode does the safe thing automatically: it locks
`trusted_proxies` to that provider's edge ranges **and** reads the
provider's dedicated client-IP header (`CF-Connecting-IP` /
`Incap-Client-IP`). Locking the ranges is essential — trusting the header
from any source would let a client hit the origin directly and forge its
IP, bypassing every ban. Because both the log parser and the bouncer read
the same resolved `client_ip`, this one setting fixes detection and
enforcement together.

The edge ranges are kept current **on the instance, in-process**: the
plugin image bundles [`caddy-cdn-ranges`](https://github.com/sarumaj/caddy-cdn-ranges),
which re-fetches each provider's published ranges on an interval
(Cloudflare's list; Imperva's open ranges API). So a long-lived instance
stays correct on its own — no vendored list in this repo, and no
`canasta upgrade` needed when a provider changes ranges. (Note this means
the instance needs outbound network access to the provider's range
endpoint.)

For any other proxy, pass a comma-separated CIDR list (uses
`X-Forwarded-For`, strict right-to-left):

```bash
canasta config set CADDY_TRUSTED_PROXIES=10.0.0.0/8,192.0.2.0/24 -i mysite
```

A typo (anything that isn't `cloudflare`, `imperva`, or valid CIDRs) is
rejected at `config set` time rather than crashing Caddy on restart.

This stays correct even if someone reaches the origin directly: Canasta
only publishes Caddy's `:80/:443` (Varnish, Apache, and the database are
internal to the Compose network and unreachable from outside), and a
direct hit doesn't come from a trusted range — so Caddy ignores its
client-IP header and attributes the real source IP, which CrowdSec can
still ban. Restricting the host firewall to the provider's ranges is
optional hardening that forces traffic through the CDN's *own*
WAF/rate-limiting layer; it isn't needed for correct IP attribution
here (and watch out for ACME HTTP-01 challenges if you do it).

## Whitelisting trusted IPs

To make sure a false positive can never lock you out, list your
office/VPN/monitoring/CDN addresses in the instance's
`config/crowdsec/whitelists.yaml`. It ships ready to edit (like
`Caddyfile.global`), is version-controlled, and is never overwritten by
upgrades:

```yaml
whitelist:
  reason: "trusted sources"
  ip:
    - "203.0.113.10"
  cidr:
    - "198.51.100.0/24"
```

CrowdSec reads parser files at start, so apply changes with
`canasta restart -i mysite`.

## Blocking and unblocking IPs

To act on an address immediately — independent of CrowdSec's automatic
detection — add or remove a decision:

```bash
canasta crowdsec ban 203.0.113.50 -i mysite                       # default 4h ban
canasta crowdsec ban 203.0.113.50 --duration 24h --reason "scraper" -i mysite
canasta crowdsec unban 203.0.113.50 -i mysite
```

`canasta crowdsec status` lists the decisions currently in effect.
(These are live decisions in the engine's database; for a *permanent*
allow, add the address to `config/crowdsec/whitelists.yaml` instead.)

## Tuning detection

The bundled `crowdsecurity/caddy` and `crowdsecurity/http-cve`
collections are installed at container start (via the `COLLECTIONS`
environment variable) and cover common HTTP attacks and CVE probes.

Installing additional hub collections is an advanced, ad-hoc action — it
writes to the engine's data volume rather than to version-controlled
config, so it is not tracked by gitops and is lost if the volume is
recreated:

```bash
canasta maintenance exec -i mysite -s crowdsec -- cscli collections install crowdsecurity/http-dos
```

Durable detection changes — whitelists, custom scenarios — belong in
version-controlled files under `config/crowdsec/`, not ad-hoc `cscli`
state, so they survive container recreation and travel with gitops.
