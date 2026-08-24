"""Shared HTTP access to the canasta.wiki MediaWiki API.

Both wiki scripts talk to the same API from the same CI runners and hit
the same failure: a request that never gets an answer, while the wiki
serves fine from elsewhere. Each had grown its own copy of the backoff
schedule, which is how two copies drift into disagreeing about how long
to wait.
"""

import http.client
import json
import sys
import time
import urllib.error
import urllib.request

HTTP_TIMEOUT = 10  # seconds; a hung wiki API must not stall the caller

RETRY_BACKOFF_BASE = 15  # seconds; doubled each retry, capped at the max
RETRY_BACKOFF_MAX = 120
MAX_CONNECT_RETRIES = 5

# Statuses that mean "the request arrived but try again". Anything else
# in the 4xx range is a real error: retrying only delays the report.
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}

# Transport failures where the request never reached the API at all.
TRANSPORT_ERRORS = (
    urllib.error.URLError,
    http.client.HTTPException,
    TimeoutError,
    ConnectionError,
)


def retry_delay(attempt):
    """Backoff (seconds) before retry number `attempt` (0-based)."""
    return min(RETRY_BACKOFF_BASE * (2 ** attempt), RETRY_BACKOFF_MAX)


def open_json(open_target, target, data=None, api_url=None,
              timeout=HTTP_TIMEOUT, sleep=None):
    """Open `target`, return the decoded JSON body, retry if unanswered.

    `open_target` is the caller's opener callable — an authenticated
    session's `opener.open` or a bare `urllib.request.urlopen` — so a
    caller keeps its own cookies and headers.

    `target` is a URL string or a prepared Request. Anything meaning the
    API never answered (connection timeout, reset, DNS, a 5xx or 429) is
    retried; a 4xx is raised straight through.

    `sleep` is resolved at call time when not given, so patching
    `time.sleep` still takes effect.
    """
    request = (
        urllib.request.Request(target, data=data)
        if isinstance(target, str) else target
    )
    where = api_url or getattr(request, "full_url", target)
    last = None
    for attempt in range(MAX_CONNECT_RETRIES + 1):
        try:
            with open_target(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # HTTPError is a URLError subclass, so it must be caught
            # first to keep 4xx from being retried.
            if exc.code not in RETRYABLE_HTTP_STATUS:
                raise
            last = exc
        except TRANSPORT_ERRORS as exc:
            last = exc
        if attempt >= MAX_CONNECT_RETRIES:
            break
        delay = retry_delay(attempt)
        print(
            "Cannot reach %s (%s); retrying in %ds (%d/%d)"
            % (where, last, delay, attempt + 1, MAX_CONNECT_RETRIES),
            file=sys.stderr,
        )
        (sleep or time.sleep)(delay)
    raise RuntimeError(
        "Could not reach the wiki API at %s after %d attempts: %s"
        % (where, MAX_CONNECT_RETRIES + 1, last)
    )
