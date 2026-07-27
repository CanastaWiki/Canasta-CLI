#!/usr/bin/env python3
"""wiki-check command — verify MediaWiki instances are accessible."""

import sys
from . import _helpers
from ._helpers import register


@register("wiki_check")
def cmd_wiki_check(args):
    instance_id, instance = _helpers._resolve_instance(args)
    host = getattr(args, "host", None) or instance.get("host") or "localhost"
    path = instance.get("path", "")
    wikis = _helpers._read_wikis(path, host)

    if not wikis:
        print(
            "Error: no wikis configured for instance '%s'" % instance_id,
            file=sys.stderr,
        )
        return 1

    print("Checking Canasta Wiki: %s" % instance_id)

    all_ok = True
    for wiki in wikis:
        wiki_id = wiki.get("id")
        # .get("url", "") returns the default only when the key is absent; a
        # present url: null still yields None, and None.strip() would crash
        # before the missing-url guard below can report it.
        wiki_url = (wiki.get("url") or "").strip()
        if not wiki_url:
            print("Wiki '%s' failed: missing wiki URL in wikis.yaml." % wiki_id)
            all_ok = False
            continue

        verdict = _helpers._probe_wiki(wiki_url, host, instance_path=path)
        if verdict == _helpers.WIKI_REACHABLE:
            print("Wiki '%s' is reachable at %s." % (wiki_id, wiki_url))
        elif verdict == _helpers.WIKI_INDETERMINATE:
            print(
                "Wiki '%s' could not be checked at %s: host '%s' is unreachable."
                % (wiki_id, wiki_url, host)
            )
            all_ok = False
        else:
            print("Wiki '%s' could not be reached at %s." % (wiki_id, wiki_url))
            all_ok = False

    return 0 if all_ok else 1
