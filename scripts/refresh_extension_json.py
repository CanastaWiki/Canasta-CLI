#!/usr/bin/env python3
"""Refresh the bundled ExtensionJson.json snapshot.

Downloads the extjsonuploader dataset from toolforge and replaces
data/ExtensionJson.json atomically, validating the download parses as JSON
before touching the file. Stdlib only; run via 'make refresh-extension-json'
or directly. See data/README.md for provenance.
"""

import json
import os
import sys
import tempfile
import urllib.request

URL = "https://extjsonuploader.toolforge.org/ExtensionJson.json"
DEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "ExtensionJson.json")


def main():
    request = urllib.request.Request(URL, headers={"User-Agent": "canasta-cli"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError("unexpected ExtensionJson.json payload")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(DEST), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(raw.decode("utf-8"))
        # Match the 0644 mode of the committed file so a working-copy refresh
        # does not leave data/ExtensionJson.json as 0600 (same fix as the
        # composer-local module).
        os.chmod(tmp, 0o644)
        os.replace(tmp, DEST)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    print("Refreshed %s (%d entries) from %s" % (DEST, len(data), URL))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.exit("Failed to refresh ExtensionJson.json: %s" % exc)
