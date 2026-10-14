# Wraps the CrowdSec image's entrypoint. The Helm chart's crowdsec sidecar
# runs this file; the Compose crowdsec service carries an identical copy.
#
# The engine does not start while the Central API (CAPI) refuses its
# credentials, and the local API the Caddy bouncer needs goes down with it.
# When a start fails that way, the credentials are kept as a .refused copy
# and replaced by an empty file (a missing one is fatal), so the engine runs
# with the local API only. Each later start tests the kept copy once and
# puts it back when CAPI accepts it, so the engine keeps its CAPI identity,
# and leaves a .restored marker for the CLI to report.
#
# A start that fails within 5 minutes for any other reason is held for 10
# minutes before the container exits, keeping restarts under the Central
# API's free-tier login limit. Signals are forwarded to the engine.

creds=/etc/crowdsec/online_api_credentials.yaml
refused="$creds.refused"
restored="$creds.restored"
stopping=0
pid=
trap 'stopping=1; [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null' TERM INT

local_only() {
    echo "CrowdSec's Central API refuses this engine's credentials; running with the local API only, so the community blocklist is unavailable." >&2
}

# True when CAPI answered and turned the credentials away. A network
# failure, a server error or rate limiting is not a refusal.
capi_refuses() {
    out=$(cscli capi status 2>&1) && return 1
    echo "$out" | grep -q 'API error:' || return 1
    ! echo "$out" | grep -qiE 'http code (5[0-9][0-9]|429)|internal server error|bad gateway|service unavailable|gateway timeout|too many requests'
}

run_engine() {
    started=$(date +%s)
    /bin/bash /docker_start.sh &
    pid=$!
    wait "$pid"
    rc=$?
    while kill -0 "$pid" 2>/dev/null; do wait "$pid"; rc=$?; done
}

failed_fast() {
    [ "$stopping" = 0 ] && [ "$rc" -ne 0 ] &&
        [ $(( $(date +%s) - started )) -lt 300 ]
}

if [ -s "$refused" ]; then
    cp "$refused" "$creds"
    if cscli capi status >/dev/null 2>&1; then
        rm -f "$refused"
        : > "$restored"
        echo "CrowdSec's Central API accepts this engine's credentials again; the community blocklist is restored." >&2
    else
        : > "$creds"
        local_only
    fi
fi

[ -e "$creds" ] && had_creds=1 || had_creds=0
run_engine

if failed_fast && [ -s "$creds" ] && capi_refuses; then
    [ -e "$refused" ] || mv "$creds" "$refused"
    : > "$creds"
    local_only
    run_engine
elif failed_fast && [ "$had_creds" = 0 ] && [ -e "$creds" ] && [ ! -s "$creds" ]; then
    # The image registers with CAPI on its first start and exits when that
    # fails, leaving an empty credentials file. A second start skips
    # registration and runs with the local API only.
    echo "CrowdSec could not register with the Central API; running with the local API only, so the community blocklist is unavailable." >&2
    run_engine
fi

if failed_fast; then
    echo "crowdsec exited with status $rc within 5 minutes of starting; waiting 10 minutes before exiting so restarts stay under the Central API login limit" >&2
    sleep 600 &
    wait $!
fi
exit "$rc"
