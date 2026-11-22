# Canasta's Caddy image: upstream Caddy plus the plugins Canasta's
# optional features need, built via xcaddy.
#
# Published as ghcr.io/canastawiki/canasta-caddy. The default caddy
# service runs stock upstream Caddy; the profile sync swaps in this image
# (via CANASTA_CADDY_IMAGE) only when a feature that needs a plugin is
# enabled — CrowdSec (CANASTA_ENABLE_CROWDSEC) or a provider trusted-proxy
# mode (TRUSTED_PROXIES=cloudflare|imperva). Every plugin here is
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
#   - caddy-combine-ip-ranges: merges several trusted_proxies IP sources
#     into one list. On Kubernetes Caddy always sits behind the in-cluster
#     ingress, so it must trust the cluster pod CIDR AND (when a CDN is in
#     front) the CDN's published ranges. This module nests `static` (the
#     cluster CIDR) and `cdn_ranges` (the CDN) inside one trusted_proxies
#     directive. Inert until the Caddyfile uses `combine` (K8s + CDN only).
#
# CADDY_VERSION is at least what the bundled plugins require, so it can
# lead the stock default caddy:<ver>-alpine in docker-compose.yml. The
# image is tagged by this Caddy version, so a plugin bump ships with a
# Caddy bump: republishing an existing tag with different plugins would
# leave two instances on the same tag running different code. Keep the
# published tag (docker-caddy.yml) in lockstep with
# canasta_caddy_plugin_image in vars/images.yml.
ARG CADDY_VERSION=2.11.9
ARG CROWDSEC_BOUNCER_VERSION=v0.14.1
ARG CDN_RANGES_VERSION=v2.1.0
ARG COMBINE_IP_RANGES_VERSION=v0.0.1

FROM caddy:${CADDY_VERSION}-builder AS builder
ARG CROWDSEC_BOUNCER_VERSION
ARG CDN_RANGES_VERSION
ARG COMBINE_IP_RANGES_VERSION
RUN xcaddy build \
    --with github.com/hslatman/caddy-crowdsec-bouncer/http@${CROWDSEC_BOUNCER_VERSION} \
    --with github.com/sarumaj/caddy-cdn-ranges/v2@${CDN_RANGES_VERSION} \
    --with github.com/fvbommel/caddy-combine-ip-ranges@${COMBINE_IP_RANGES_VERSION}

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
