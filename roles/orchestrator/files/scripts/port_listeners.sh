#!/bin/bash
# Print what listens on the given TCP ports of this host, one line per
# listener, or CANASTA_PORT_PROBE_UNAVAILABLE when that cannot be told.
#
# Usage: port_listeners.sh <runtime> <image> <port>...
#
# Inside the CLI's own container, ss and lsof see the container's network,
# not the host's; there, and on a host with neither tool, a short-lived
# container on the host's network answers instead.
runtime=$1
image=$2
shift 2

in_container() { [ -f /.dockerenv ] || [ -f /run/.containerenv ]; }

if ! in_container && command -v ss >/dev/null 2>&1; then
    filter=$(printf 'sport = :%s or ' "$@")
    ss -tlnpH "${filter% or }" 2>/dev/null
    exit 0
fi
if ! in_container && command -v lsof >/dev/null 2>&1; then
    args=()
    for port in "$@"; do args+=("-iTCP:$port"); done
    lsof -nP "${args[@]}" -sTCP:LISTEN 2>/dev/null | tail -n +2
    exit 0
fi
if out=$("$runtime" run --rm --network host --entrypoint netstat "$image" -tln 2>/dev/null); then
    for port in "$@"; do
        printf '%s\n' "$out" | awk -v p=":$port" \
            '$6 == "LISTEN" && substr($4, length($4) - length(p) + 1) == p'
    done
    exit 0
fi
echo CANASTA_PORT_PROBE_UNAVAILABLE
