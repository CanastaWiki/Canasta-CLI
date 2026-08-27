"""Every download in the Dockerfile has to survive a connection reset.

curl's plain --retry covers HTTP 5xx, 408, 429 and timeouts. A reset or
TLS-level failure is outside that set, so a `--retry`-only invocation
reads as protective while failing the build on first contact — which is
what took out the 4.17.0 release build. `tag` needs `build`, so a
transient reset leaves main with a bumped VERSION, no image and no tag.

This test fails when a curl is added without the wider retry flags.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DOCKERFILE = os.path.join(REPO_ROOT, "Dockerfile")


def _curl_invocations():
    """The argument text of each curl call, one entry per call.

    Flags are split across backslash continuations, and the kubectl step
    nests a second `$(curl ...)` inside the first — so continuations are
    joined and every `curl` on the resulting logical line is split out,
    rather than letting one match swallow the calls that follow it.
    """
    with open(DOCKERFILE) as f:
        content = f.read()
    # Drop comment lines so prose mentioning curl is not treated as code.
    code = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    invocations = []
    for line in code.replace("\\\n", " ").splitlines():
        invocations.extend(re.split(r"\bcurl\b", line)[1:])
    return invocations


class TestDockerfileCurlRetries:
    def test_the_dockerfile_still_downloads_with_curl(self):
        assert _curl_invocations(), (
            "no curl invocations found in the Dockerfile — this test's "
            "parsing has drifted from the file it guards"
        )

    def test_every_retrying_curl_retries_connection_level_failures(self):
        weak = [
            args.strip()
            for args in _curl_invocations()
            if "--retry" in args and "--retry-all-errors" not in args
        ]
        assert not weak, (
            "these curl invocations retry HTTP errors but not connection "
            "resets; add --retry-all-errors --retry-connrefused: %s" % weak
        )
