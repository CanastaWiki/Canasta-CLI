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
2. Switches the caddy service to the bouncer-enabled image variant
   `ghcr.io/canastawiki/canasta-caddy-crowdsec` by setting the managed
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

`CANASTA_CADDY_IMAGE` is managed automatically by the CrowdSec toggle,
but only when it is empty or already holds the managed crowdsec image. If
you set it to your own value (e.g. a Caddy build with additional
plugins), the toggle will not overwrite it. In that case you are
responsible for ensuring your image includes the
[`caddy-crowdsec-bouncer`](https://github.com/hslatman/caddy-crowdsec-bouncer)
HTTP module, or the rendered `crowdsec` directive will fail to load.

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

## Tuning detection

The bundled `crowdsecurity/caddy` and `crowdsecurity/http-cve`
collections are installed at container start (via the `COLLECTIONS`
environment variable). For ad-hoc inspection or to install more
collections, run `cscli` in the container via `canasta maintenance exec`
rather than reaching for bare docker:

```bash
canasta maintenance exec -i mysite -s crowdsec -- cscli decisions list
canasta maintenance exec -i mysite -s crowdsec -- cscli collections install crowdsecurity/http-dos
```

(Durable detection changes — whitelists, custom scenarios — belong in
version-controlled files under `config/crowdsec/`, not ad-hoc `cscli`
state, so they survive container recreation and travel with gitops.)
