# Canasta's Caddy image: upstream Caddy plus the CrowdSec bouncer, built
# via xcaddy.
#
# Published as ghcr.io/canastawiki/canasta-caddy. The default caddy
# service runs stock upstream Caddy; the profile sync swaps in this image
# (via CANASTA_CADDY_IMAGE) only while CrowdSec is enabled
# (CANASTA_ENABLE_CROWDSEC). The bouncer is inert until the rendered
# Caddyfile uses it, so this image is a safe drop-in for stock Caddy.
# Trusted proxies and CDN address ranges are the HAProxy edge's job, so
# no plugin for them is needed here.
#
# CADDY_VERSION is at least what the bouncer requires, so it can lead the
# stock default caddy:<ver>-alpine in docker-compose.yml. The image is
# tagged by this Caddy version, with a -<n> revision when the plugin set
# changes on the same Caddy version, so that every published tag has one
# build behind it. Keep the published tag (docker-caddy.yml) in lockstep
# with canasta_caddy_plugin_image in vars/images.yml.
ARG CADDY_VERSION=2.11.9
ARG CROWDSEC_BOUNCER_VERSION=v0.14.1

FROM caddy:${CADDY_VERSION}-builder AS builder
ARG CROWDSEC_BOUNCER_VERSION
RUN xcaddy build \
    --with github.com/hslatman/caddy-crowdsec-bouncer/http@${CROWDSEC_BOUNCER_VERSION}

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
