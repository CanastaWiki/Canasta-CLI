"""Tests for scripts/wiki_http.py, the shared MediaWiki API access.

The publish script and the example validator talk to the same API from
the same runners and hit the same unanswered-request failure. They used
to carry a copy of the backoff schedule each; the point of this module
is that there is now one, so the drift guard below matters as much as
the behavior tests.
"""

import os
import sys
import time
import urllib.error

import pytest

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts"),
)
sys.path.insert(0, SCRIPTS_DIR)

import wiki_http  # noqa: E402
import wiki_publish  # noqa: E402
import validate_wiki_examples  # noqa: E402


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


def _http_error(code):
    return urllib.error.HTTPError("https://w/api.php", code, "x", {}, None)


class TestRetryDelay:
    def test_doubles_then_caps(self):
        delays = [wiki_http.retry_delay(i) for i in range(6)]
        assert delays == [15, 30, 60, 120, 120, 120]


class TestOpenJson:
    def test_decodes_the_body(self):
        opener = _Opener(b'{"ok": true}')
        assert wiki_http.open_json(opener, "https://w/api.php") == {"ok": True}

    def test_retries_transport_failure(self):
        opener = _Opener(urllib.error.URLError("timed out"), b'{"ok": 1}')
        slept = []
        assert wiki_http.open_json(
            opener, "https://w/api.php", sleep=slept.append,
        ) == {"ok": 1}
        assert opener.calls == 2
        assert slept == [15]

    def test_gives_up_with_a_clear_error(self):
        opener = _Opener(*[urllib.error.URLError("x")] * 10)
        with pytest.raises(RuntimeError, match="Could not reach the wiki API"):
            wiki_http.open_json(
                opener, "https://w/api.php", sleep=lambda _: None,
            )
        assert opener.calls == wiki_http.MAX_CONNECT_RETRIES + 1

    @pytest.mark.parametrize("code", sorted(wiki_http.RETRYABLE_HTTP_STATUS))
    def test_retryable_statuses(self, code):
        opener = _Opener(_http_error(code), b'{"ok": 1}')
        assert wiki_http.open_json(
            opener, "https://w/api.php", sleep=lambda _: None,
        ) == {"ok": 1}
        assert opener.calls == 2

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 409])
    def test_client_errors_are_not_retried(self, code):
        """The request arrived and was rejected; retrying only delays
        the report."""
        opener = _Opener(_http_error(code))
        with pytest.raises(urllib.error.HTTPError):
            wiki_http.open_json(
                opener, "https://w/api.php", sleep=lambda _: None,
            )
        assert opener.calls == 1

    def test_sleep_defaults_to_time_sleep_at_call_time(self, monkeypatch):
        """Resolved per call, not captured at import, so patching
        time.sleep still takes effect."""
        slept = []
        monkeypatch.setattr(time, "sleep", slept.append)
        opener = _Opener(urllib.error.URLError("x"), b'{"ok": 1}')
        wiki_http.open_json(opener, "https://w/api.php")
        assert slept == [15]


class TestNoDuplicateSchedules:
    """A second copy of these numbers is what the extraction removed."""

    def test_publisher_shares_the_schedule(self):
        assert wiki_publish.RETRY_BACKOFF_BASE is wiki_http.RETRY_BACKOFF_BASE
        assert wiki_publish.RETRY_BACKOFF_MAX is wiki_http.RETRY_BACKOFF_MAX
        assert wiki_publish.MAX_CONNECT_RETRIES is wiki_http.MAX_CONNECT_RETRIES
        assert wiki_publish.HTTP_TIMEOUT is wiki_http.HTTP_TIMEOUT
        assert wiki_publish._retry_delay is wiki_http.retry_delay

    def test_validator_shares_the_schedule(self):
        assert (validate_wiki_examples.MAX_RETRIES
                is wiki_http.MAX_CONNECT_RETRIES)

    def test_neither_module_redefines_the_backoff(self):
        for module in (wiki_publish, validate_wiki_examples):
            source = open(module.__file__).read()
            assert "RETRY_BACKOFF_BASE = 15" not in source, (
                "%s redefines the backoff instead of using wiki_http"
                % os.path.basename(module.__file__)
            )
