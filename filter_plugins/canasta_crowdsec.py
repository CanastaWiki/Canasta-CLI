# Jinja filters for the CrowdSec integration.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire because Canasta drives everything through
# include_role / include_tasks rather than the play-level `roles:` keyword).


def _is_caddy_family(bouncer, family_name):
    name = str(bouncer.get("name", "")) if isinstance(bouncer, dict) else ""
    return name == family_name or name.startswith(family_name + "@")


def _parse_last_pull(value):
    """An aware UTC datetime from a cscli RFC3339 timestamp, or None."""
    import datetime
    import re

    m = re.match(
        r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?"
        r"(Z|[+-]\d\d:\d\d)?$",
        str(value or "").strip(),
    )
    if not m:
        return None
    when = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    offset = m.group(2)
    if offset and offset != "Z":
        sign = 1 if offset[0] == "+" else -1
        when -= sign * datetime.timedelta(
            hours=int(offset[1:3]), minutes=int(offset[4:6]))
    return when.replace(tzinfo=datetime.timezone.utc)


FAMILY_RESET_HINTS = {
    "canasta-caddy": " Run 'canasta crowdsec bouncer-enroll' to reset to a "
                     "single registration.",
}


def canasta_crowdsec_status_bouncers(bouncers, family_name=("canasta-caddy", "canasta-edge"),
                                     now=None, stale_after_seconds=120):
    """Render a de-duplicated 'Bouncers' summary for ``crowdsec status``.

    Every time the caddy container reconnects from a new container IP,
    CrowdSec auto-creates a ``canasta-caddy@<ip>`` child registration, so the
    raw ``cscli bouncers list`` accumulates stale rows that all report
    ``valid`` even though only the newest one pulls. These children are
    undeletable in CrowdSec on their own (bouncer-enroll resets the family
    by re-creating the parent with the same key), so the status command
    collapses the family for display: show the single live registration
    (most-recent ``last_pull``) plus a count of the harmless duplicates. Any
    non-caddy bouncers are listed verbatim. The edge's bouncer
    (``canasta-edge``) reconnects the same way and is collapsed alike.
    ``family_name`` is one family or several.

    The live registration is "active" only while its last pull is recent.
    The Caddy bouncer pulls every 15 seconds, so a last pull older than
    ``stale_after_seconds`` means it has stopped pulling (Caddy is running
    without it, or is down). ``now`` is the engine's current time, as an
    aware datetime or an RFC3339 string; it defaults to the current UTC time.

    Returns a pre-formatted, indented block of lines (or ``(none)``) so the
    status playbook can drop it straight into its message.
    """
    import datetime

    if not isinstance(now, datetime.datetime):
        now = _parse_last_pull(now)
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    bouncers = [b for b in (bouncers or []) if isinstance(b, dict)]
    names = [family_name] if isinstance(family_name, str) else list(family_name)
    others = [b for b in bouncers if not any(_is_caddy_family(b, n) for n in names)]

    lines = []
    for name in names:
        family = [b for b in bouncers if _is_caddy_family(b, name)]
        if family:
            lines += _family_lines(family, name, now, stale_after_seconds)

    for b in others:
        lines.append(
            "  %s — IP %s, last pull %s" % (
                b.get("name", "?"),
                b.get("ip_address") or "?",
                b.get("last_pull") or "never",
            )
        )

    return "\n".join(lines) if lines else "  (none)"


def _family_lines(family, family_name, now, stale_after_seconds):
    lines = []
    if family:
        # last_pull is an RFC3339 UTC timestamp; lexicographic max == latest.
        # A never-pulled row sorts oldest, so a live puller always wins.
        live = max(family, key=lambda b: b.get("last_pull") or "")
        last = _parse_last_pull(live.get("last_pull"))
        if last is None:
            state = "not pulling (IP %s, no pull yet)" % (
                live.get("ip_address") or "?")
        else:
            age = int((now - last).total_seconds())
            if age > stale_after_seconds:
                state = "stale (IP %s, last pull %s, %d min ago)" % (
                    live.get("ip_address") or "?",
                    live.get("last_pull"),
                    age // 60,
                )
            else:
                state = "active (IP %s, last pull %s)" % (
                    live.get("ip_address") or "?",
                    live.get("last_pull"),
                )
        lines.append("  %s — %s" % (live.get("name", "?"), state))
        stale = len(family) - 1
        if stale > 0:
            lines.append(
                "  + %d stale auto-created duplicate registration(s) from past "
                "container restarts (harmless — only the newest registration "
                "above pulls decisions).%s" % (stale, FAMILY_RESET_HINTS.get(family_name, ""))
            )
    return lines


def canasta_crowdsec_blocklist_breakdown(raw):
    """Per-blocklist IP counts from ``cscli decisions list --origin lists -o raw``.

    The raw output is CSV with columns ``id,source,ip,reason,...`` where
    ``reason`` is the subscribed blocklist's name (e.g. ``otx-webscanners``).
    Console-blocklist decisions number in the thousands and are excluded from
    the default ``cscli decisions list``, so this groups them by list and
    returns a ready-to-append block headed by ``Console blocklists in effect
    (cached locally):`` and tailed by a note on how to clear them — or ``""``
    when there are none. The header avoids "subscribed" because, from inside
    the engine, a still-subscribed list and one left over after a Console
    unsubscribe/engine-deletion look identical.

    All newlines are produced here (Jinja string literals do not interpret
    ``\\n``), so the caller can concatenate the result directly.
    """
    import csv
    import io

    text = (raw or "").strip()
    if not text:
        return ""
    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0][:4] == ["id", "source", "ip", "reason"]:
        rows = rows[1:]  # drop the header

    counts = {}
    for row in rows:
        if len(row) >= 4:
            counts[row[3]] = counts.get(row[3], 0) + 1
    if not counts:
        return ""

    width = max(len(name) for name in counts)
    lines = ["    %-*s  %d" % (width, name, counts[name])
             for name in sorted(counts)]
    note = (
        "\n\n  These stay enforced even after you unsubscribe or remove the"
        "\n  engine in the Console. Clear them with:"
        "\n    canasta crowdsec reload --purge-blocklist <name>   (one list)"
        "\n    canasta crowdsec reload --purge-blocklists         (all)"
    )
    return ("\n  Console blocklists in effect (cached locally):\n"
            + "\n".join(lines) + note)


def canasta_crowdsec_capi_line(last_pull_json, registered, ip_count,
                               now=None, stale_after_hours=3, refused=False):
    """The ``Central API (community blocklist):`` line for ``crowdsec status``.

    ``last_pull_json`` is ``cscli alerts list --origin CAPI -l 1 -o json``:
    each community-blocklist pull that brings decisions records an
    ``update : +N/-M IPs`` alert, and the engine itself treats the newest one
    as its last pull when deciding whether to pull again. Pulls run every
    2 hours, so a newest alert older than ``stale_after_hours`` means pulls
    are failing even though the engine is registered and still holds
    ``ip_count`` decisions from earlier pulls.

    ``registered`` is whether the engine has Central API credentials;
    ``now`` is an aware datetime, defaulting to the current UTC time.
    ``refused`` is whether the engine keeps credentials the Central API
    refused or did not answer, which leaves it running local-only.
    """
    import datetime
    import json

    if refused:
        return ("refusing this engine's credentials or not answering (no "
                "community blocklist; the engine runs with its local API "
                "only and retries its kept credentials at each engine start)")
    if not registered:
        return ("not registered (no community blocklist; auto-registers on "
                "the next start)")

    try:
        count = int(str(ip_count).strip() or 0)
    except ValueError:
        count = 0

    try:
        alerts = json.loads(last_pull_json or "null") or []
    except ValueError:
        alerts = []
    created = alerts[0].get("created_at") if alerts and isinstance(
        alerts[0], dict) else None
    last = None
    if created:
        try:
            last = datetime.datetime.strptime(
                created, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            last = None

    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    loaded = "%d IPs still loaded from earlier pulls" % count

    if last is None:
        return ("registered — WARNING: no community-blocklist update yet"
                + ("; %s" % loaded if count else "")
                + ". Check the crowdsec container logs for Central API "
                "errors.")

    when = last.strftime("%Y-%m-%d %H:%M UTC")
    if now - last > datetime.timedelta(hours=stale_after_hours):
        return ("registered — WARNING: no community-blocklist update since "
                "%s (expected every 2 hours)" % when
                + ("; %s" % loaded if count else "")
                + ". Check the crowdsec container logs for Central API "
                "errors.")

    detail = ("%d IPs loaded, last update %s" % (count, when)) if count \
        else "last update %s" % when
    return "registered — community blocklist active (%s)" % detail


def _parse_cscli_time(value):
    """An aware datetime from a cscli JSON timestamp (RFC 3339, with or
    without fractional seconds), or None."""
    import datetime
    import re

    match = re.match(
        r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)$",
        str(value or ""))
    if not match:
        return None
    zone = "+00:00" if match.group(2) == "Z" else match.group(2)
    return datetime.datetime.fromisoformat(match.group(1) + zone)


def canasta_crowdsec_agent_line(machines_json, agent_name, started_at=None,
                                now=None, stale_after_minutes=5,
                                starting_minutes=3):
    """The ``Agent:`` line for ``crowdsec status`` on Kubernetes.

    ``machines_json`` is ``cscli machines list -o json`` from the local API.
    The agent in the caddy pod is the only thing that parses the access log,
    so an agent that stopped reporting leaves the engine healthy but
    detecting nothing. It sends heartbeats regularly while it runs, so one
    older than ``stale_after_minutes`` means it has stopped.

    ``started_at`` lists the start times (RFC 3339, space-separated) of the
    caddy and local API pods. For ``starting_minutes`` after the newest of
    them, an agent that has not reported yet is starting, not failing.
    ``now`` is an aware datetime, defaulting to the current UTC time.
    """
    import datetime
    import json

    try:
        machines = json.loads(machines_json or "null") or []
    except ValueError:
        machines = []
    agent = next((m for m in machines if isinstance(m, dict)
                  and m.get("machineId") == agent_name), None)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    last = _parse_cscli_time(agent.get("last_heartbeat")) if agent else None
    if last is not None and \
            now - last <= datetime.timedelta(minutes=stale_after_minutes):
        return "reporting (last heartbeat %s)" % last.strftime(
            "%Y-%m-%d %H:%M UTC")

    starts = [t for t in (_parse_cscli_time(v)
                          for v in str(started_at or "").split()) if t]
    if starts:
        age = now - max(starts)
        if age <= datetime.timedelta(minutes=starting_minutes):
            return ("starting (pods restarted %d s ago; the agent reports "
                    "once it has logged in)" % max(0, age.total_seconds()))

    if agent is None:
        return ("WARNING: not registered with the local API, so nothing is "
                "parsing the Caddy access log. Run 'canasta crowdsec reload' "
                "to register it.")
    if last is None:
        return ("WARNING: registered but has never reported, so nothing is "
                "parsing the Caddy access log. Check the crowdsec-agent "
                "container logs in the caddy pod.")
    return ("WARNING: no report since %s, so the Caddy access log is "
            "not being parsed. Check the crowdsec-agent container logs "
            "in the caddy pod." % last.strftime("%Y-%m-%d %H:%M UTC"))


class FilterModule(object):
    def filters(self):
        return {
            "canasta_crowdsec_status_bouncers": canasta_crowdsec_status_bouncers,
            "canasta_crowdsec_blocklist_breakdown":
                canasta_crowdsec_blocklist_breakdown,
            "canasta_crowdsec_capi_line": canasta_crowdsec_capi_line,
            "canasta_crowdsec_agent_line": canasta_crowdsec_agent_line,
        }
