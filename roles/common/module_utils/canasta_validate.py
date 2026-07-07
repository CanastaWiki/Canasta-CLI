# -*- coding: utf-8 -*-
"""Shared validation helpers for canasta_* Ansible modules.

Both canasta_wikis_yaml and canasta_farmsettings need to validate wiki
IDs the same way. Pre-extraction this file existed twice — once in
each module — with a "Keep both copies in sync" comment that did not
keep them in sync. Modules in `roles/common/library/` import this via
`from ansible.module_utils.canasta_validate import …` (Ansible
automatically adds each role's `module_utils/` directory to the
module loader's search path).
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re


# Wiki IDs that collide with paths the canasta image expects
# canasta-controlled (the `wikis/` mount, the `images/` images dir,
# `w/` and `wiki/` URL prefixes, and `settings/` per-wiki overrides).
# Identical list shared by both modules.
RESERVED_WIKI_IDS = ["settings", "images", "w", "wiki", "wikis"]

# A wiki ID becomes a MariaDB database name, a per-wiki settings directory
# (config/settings/wikis/<id>/), and a URL path component. Restrict it to
# alphanumerics and underscores. Unlike instance IDs, hyphens are NOT
# allowed.
_WIKI_ID_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def validate_wiki_id(value):
    """Return None if `value` is a valid wiki ID, otherwise an error
    string suitable for `module.fail_json(msg=...)`."""
    if not value:
        return "wiki ID cannot be empty"
    if "-" in value:
        return "wiki ID '%s' cannot contain hyphens" % value
    if not _WIKI_ID_RE.match(value):
        return (
            "wiki ID '%s' is invalid: use letters, digits, and underscores "
            "only" % value
        )
    if value in RESERVED_WIKI_IDS:
        return "wiki ID '%s' is reserved (cannot be: %s)" % (
            value, ", ".join(RESERVED_WIKI_IDS),
        )
    return None


# Sidecar names that collide with the stack's own services. A sidecar
# becomes a Compose service, a k8s object name, and a DNS hostname, so a
# clash with one of these would override / collide with a core component
# (catastrophic, not a benign clash).
RESERVED_SIDECAR_NAMES = [
    "web", "db", "caddy", "varnish", "crowdsec", "elasticsearch",
    "jobrunner", "logstash", "opensearch", "opensearch-dashboards",
    "mediawiki", "registry",
]

# DNS label: lowercase alphanumeric and hyphens, no leading/trailing
# hyphen, 1-63 chars. Unlike a wiki ID, a sidecar name MAY contain
# hyphens (e.g. `receipt-scanner`) since it is used as a DNS name.
_SIDECAR_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def validate_sidecar_name(value):
    """Return None if `value` is a valid sidecar name, otherwise an error
    string suitable for `module.fail_json(msg=...)`."""
    if not value:
        return "sidecar name cannot be empty"
    if len(value) > 63:
        return "sidecar name '%s' is too long (max 63 characters)" % value
    if not _SIDECAR_NAME_RE.match(value):
        return (
            "sidecar name '%s' is invalid: use lowercase letters, digits, and "
            "hyphens (a DNS label — no leading/trailing hyphen)" % value
        )
    if value in RESERVED_SIDECAR_NAMES:
        return "sidecar name '%s' is reserved (cannot be: %s)" % (
            value, ", ".join(RESERVED_SIDECAR_NAMES),
        )
    return None
