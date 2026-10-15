# Jinja filters for container state reported by docker or podman inspect.
#
# Loaded via the `filter_plugins` path in ansible.cfg (see canasta_crowdsec.py).

import calendar
import os
import re
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "roles", "common", "module_utils"))

from canasta_web_cause import web_startup_cause  # noqa: E402

# Docker prints RFC 3339 ("2026-10-13T12:34:56.123456789Z"); podman prints
# Go's time.Time ("2026-10-13 12:34:56.123456789 -0400 EDT").
_STARTED_AT = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"\s*(Z|[+-]\d{2}:?\d{2})(?:\s|$)"
)


def container_started_epoch(value):
    """A container's .State.StartedAt as seconds since the epoch, or None
    when the value is not a timestamp either runtime prints."""
    match = _STARTED_AT.match(str(value or ""))
    if not match:
        return None
    year, month, day, hour, minute, second = (
        int(g) for g in match.group(1, 2, 3, 4, 5, 6))
    epoch = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
    if match.group(7):
        epoch += float("0." + match.group(7))
    zone = match.group(8)
    if zone != "Z":
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        epoch -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return epoch


class FilterModule:
    def filters(self):
        return {
            "container_started_epoch": container_started_epoch,
            "web_startup_cause": web_startup_cause,
        }
