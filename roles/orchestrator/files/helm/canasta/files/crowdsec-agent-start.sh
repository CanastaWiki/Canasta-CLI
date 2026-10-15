# Runs the CrowdSec agent in the caddy pod. Every container counts toward
# the pod's readiness, so this never exits on an agent failure: an agent that
# stops (for example while the local API is still starting) is restarted here,
# waiting longer after each quick failure, up to 5 minutes.
#
# The acquisition and whitelist files are copied in from the ConfigMap at
# start; the pod restarts when they change.

stopping=0
pid=
trap 'stopping=1; [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null' TERM INT

mkdir -p /etc/crowdsec/acquis.d /etc/crowdsec/parsers/s02-enrich
cp /cm/acquis.yaml /etc/crowdsec/acquis.d/caddy.yaml
cp /cm/whitelists.yaml /etc/crowdsec/parsers/s02-enrich/canasta-whitelists.yaml

delay=5
while [ "$stopping" = 0 ]; do
    started=$(date +%s)
    /bin/bash /docker_start.sh &
    pid=$!
    wait "$pid"
    rc=$?
    while kill -0 "$pid" 2>/dev/null; do wait "$pid"; rc=$?; done
    pid=
    [ "$stopping" = 1 ] && break
    if [ $(( $(date +%s) - started )) -ge 300 ]; then
        delay=5
    fi
    echo "crowdsec agent exited with status $rc; restarting in ${delay}s" >&2
    sleep "$delay" &
    wait $!
    delay=$(( delay * 2 ))
    [ "$delay" -gt 300 ] && delay=300
done
exit 0
