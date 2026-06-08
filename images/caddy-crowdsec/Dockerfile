# Caddy with the CrowdSec HTTP bouncer plugin, built via xcaddy.
#
# Published as ghcr.io/canastawiki/canasta-caddy-crowdsec and used as the
# caddy image variant when CANASTA_ENABLE_CROWDSEC=true. It mirrors the
# upstream caddy:<version>-alpine that the default caddy service runs,
# adding only the bouncer module. The plugin is inert until the rendered
# Caddyfile emits a `crowdsec` directive, so this image is a safe
# drop-in replacement even before a bouncer is enrolled.
#
# Keep CADDY_VERSION in lockstep with the caddy service image tag in
# roles/orchestrator/files/compose/docker-compose.yml, and the published
# tag in lockstep with _CADDY_CROWDSEC_IMAGE / the sync_compose_profiles
# literal.
ARG CADDY_VERSION=2.10.2
ARG CROWDSEC_BOUNCER_VERSION=v0.13.1

FROM caddy:${CADDY_VERSION}-builder AS builder
ARG CROWDSEC_BOUNCER_VERSION
RUN xcaddy build \
    --with github.com/hslatman/caddy-crowdsec-bouncer/http@${CROWDSEC_BOUNCER_VERSION}

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
