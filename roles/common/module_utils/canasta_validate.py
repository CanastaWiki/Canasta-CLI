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


# Wiki IDs that collide with paths the canasta image expects
# canasta-controlled (the `wikis/` mount, the `images/` images dir,
# `w/` and `wiki/` URL prefixes, and `settings/` per-wiki overrides).
# Identical list shared by both modules.
RESERVED_WIKI_IDS = ["settings", "images", "w", "wiki", "wikis"]


def validate_wiki_id(value):
    """Return None if `value` is a valid wiki ID, otherwise an error
    string suitable for `module.fail_json(msg=...)`.

    Mirrors the Go CLI's ValidateWikiID."""
    if not value:
        return "wiki ID cannot be empty"
    if "-" in value:
        return "wiki ID '%s' cannot contain hyphens" % value
    if value in RESERVED_WIKI_IDS:
        return "wiki ID '%s' is reserved (cannot be: %s)" % (
            value, ", ".join(RESERVED_WIKI_IDS),
        )
    return None
