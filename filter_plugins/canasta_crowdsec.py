# Jinja filters for the CrowdSec Compose integration.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire because Canasta drives everything through
# include_role / include_tasks rather than the play-level `roles:` keyword).


def _is_caddy_family(bouncer, family_name):
    name = str(bouncer.get("name", "")) if isinstance(bouncer, dict) else ""
    return name == family_name or name.startswith(family_name + "@")


def canasta_crowdsec_status_bouncers(bouncers, family_name="canasta-caddy"):
    """Render a de-duplicated 'Bouncers' summary for ``crowdsec status``.

    Every time the caddy container reconnects from a new container IP,
    CrowdSec auto-creates a ``canasta-caddy@<ip>`` child registration, so the
    raw ``cscli bouncers list`` accumulates stale rows that all report
    ``valid`` even though only the newest one pulls. These children are
    undeletable in CrowdSec (deleting the parent cascades and revokes the
    shared key), so the status command instead collapses the family for
    display: show the single live registration (most-recent ``last_pull``)
    plus a count of the harmless duplicates. Any non-caddy bouncers are listed
    verbatim.

    Returns a pre-formatted, indented block of lines (or ``(none)``) so the
    status playbook can drop it straight into its message.
    """
    bouncers = [b for b in (bouncers or []) if isinstance(b, dict)]
    family = [b for b in bouncers if _is_caddy_family(b, family_name)]
    others = [b for b in bouncers if not _is_caddy_family(b, family_name)]

    lines = []
    if family:
        # last_pull is an RFC3339 UTC timestamp; lexicographic max == latest.
        # A never-pulled row sorts oldest, so a live puller always wins.
        live = max(family, key=lambda b: b.get("last_pull") or "")
        lines.append(
            "  %s — active (IP %s, last pull %s)" % (
                live.get("name", "?"),
                live.get("ip_address") or "?",
                live.get("last_pull") or "never",
            )
        )
        stale = len(family) - 1
        if stale > 0:
            lines.append(
                "  + %d stale auto-created duplicate registration(s) from past "
                "container restarts (harmless — only the active bouncer above "
                "pulls decisions). CrowdSec cannot delete these individually; "
                "run 'canasta crowdsec bouncer-enroll --force' to reset to a "
                "single registration." % stale
            )

    for b in others:
        lines.append(
            "  %s — IP %s, last pull %s" % (
                b.get("name", "?"),
                b.get("ip_address") or "?",
                b.get("last_pull") or "never",
            )
        )

    return "\n".join(lines) if lines else "  (none)"


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
                               now=None, stale_after_hours=3):
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
    """
    import datetime
    import json

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


class FilterModule(object):
    def filters(self):
        return {
            "canasta_crowdsec_status_bouncers": canasta_crowdsec_status_bouncers,
            "canasta_crowdsec_blocklist_breakdown":
                canasta_crowdsec_blocklist_breakdown,
            "canasta_crowdsec_capi_line": canasta_crowdsec_capi_line,
        }
