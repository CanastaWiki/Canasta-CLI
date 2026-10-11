"""Config checksums must not tell a missing configData key from an empty one.

The CLI deploys <instance>/_chart, whose defaults set every configData key
to {}. Argo CD renders the gitops repository, whose values.yaml comes from
the instance's merged values and can lack a key (configData.db always
does). Hashed raw, null and {} differ, so the two renders disagreed and
every CLI deploy rolled the pod that Argo CD then rolled back.
"""

import os
import re

TEMPLATES = os.path.join(os.path.dirname(__file__), "..", "..", "roles",
                         "orchestrator", "files", "helm", "canasta",
                         "templates")
CHECKSUM = re.compile(
    r"checksum/[a-z-]+:\s*\{\{\s*\.Values\.configData\.(\w+)\s*\|([^}]*)\}\}")


def _checksums():
    found = []
    for name in sorted(os.listdir(TEMPLATES)):
        with open(os.path.join(TEMPLATES, name)) as f:
            for key, pipeline in CHECKSUM.findall(f.read()):
                found.append((name, key, pipeline))
    return found


def test_the_chart_has_config_checksums():
    keys = {key for _, key, _ in _checksums()}
    assert {"web", "env", "caddy", "varnish", "db", "crowdsec"} <= keys


def test_every_config_checksum_treats_a_missing_key_as_empty():
    for name, key, pipeline in _checksums():
        assert re.match(r"\s*default (dict|\(dict [^|]*\))\s*\|\s*toJson",
                        pipeline), (
            "%s: checksum of configData.%s must use 'default dict' before "
            "toJson" % (name, key))
