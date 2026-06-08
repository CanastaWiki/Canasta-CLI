# Canasta's Caddy image: upstream Caddy plus the plugins Canasta's
# optional features need, built via xcaddy.
#
# Published as ghcr.io/canastawiki/canasta-caddy. The default caddy
# service runs stock upstream Caddy; the profile sync swaps in this image
# (via CANASTA_CADDY_IMAGE) only when a feature that needs a plugin is
# enabled — CrowdSec (CANASTA_ENABLE_CROWDSEC) or a provider trusted-proxy
# mode (CADDY_TRUSTED_PROXIES=cloudflare|imperva). Every plugin here is
# inert until the rendered Caddyfile uses it, so this image is a safe
# drop-in for stock Caddy.
#
# Plugins:
#   - caddy-crowdsec-bouncer: enforces CrowdSec decisions at the edge.
#   - caddy-cdn-ranges: keeps trusted_proxies current in-process by
#     fetching the CDN/WAF's published ranges on an interval (Cloudflare
#     built-in; Imperva via its open ranges API). This is why a
#     long-lived instance stays correct without any repo-side IP list or
#     a 'canasta upgrade'.
#
# Keep CADDY_VERSION in lockstep with the caddy service image tag in
# roles/orchestrator/files/compose/docker-compose.yml, and the published
# tag in lockstep with _CADDY_PLUGIN_IMAGE / the sync_compose_profiles
# literal.
ARG CADDY_VERSION=2.10.2
ARG CROWDSEC_BOUNCER_VERSION=v0.13.1
ARG CDN_RANGES_VERSION=v2.1.0

FROM caddy:${CADDY_VERSION}-builder AS builder
ARG CROWDSEC_BOUNCER_VERSION
ARG CDN_RANGES_VERSION
RUN xcaddy build \
    --with github.com/hslatman/caddy-crowdsec-bouncer/http@${CROWDSEC_BOUNCER_VERSION} \
    --with github.com/sarumaj/caddy-cdn-ranges/v2@${CDN_RANGES_VERSION}

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
