#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for managing Canasta wikis.yaml files.

Replaces the Go internal/farmsettings package. Provides CRUD operations
on the wikis.yaml file that defines wiki farm configurations.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_wikis_yaml
short_description: Manage Canasta wikis.yaml
description:
  - Read, add, and remove wikis from the wikis.yaml farm configuration.
  - Compatible with the Go CLI's wikis.yaml format.
options:
  instance_path:
    description: Path to the Canasta instance directory.
    type: str
    required: true
  state:
    description: Action to perform.
    type: str
    choices: [read, generate, add, remove, query]
    default: read
  wiki_id:
    description: Wiki ID.
    type: str
  domain:
    description: Domain name for the wiki URL.
    type: str
  wiki_path:
    description: URL path suffix (e.g. 'docs' for example.com/docs).
    type: str
  site_name:
    description: Display name of the wiki.
    type: str
"""

import os
import re

import yaml

from ansible.module_utils.basic import AnsibleModule


RESERVED_WIKI_IDS = ["settings", "images", "w", "wiki", "wikis"]
WIKI_ID_INVALID_CHARS = re.compile(r"-")


def wikis_yaml_path(instance_path):
    """Return the path to wikis.yaml."""
    return os.path.join(instance_path, "config", "wikis.yaml")


def validate_wiki_id(wiki_id):
    """Validate a wiki ID (matching Go ValidateWikiID).

    NOTE: This validation is intentionally duplicated from canasta_farmsettings.py
    because Ansible modules in different role library/ directories cannot import
    each other. Keep both copies in sync.
    """
    if not wiki_id:
        return "wiki ID cannot be empty"
    if WIKI_ID_INVALID_CHARS.search(wiki_id):
        return "wiki ID '%s' cannot contain hyphens" % wiki_id
    if wiki_id in RESERVED_WIKI_IDS:
        return "wiki ID '%s' is reserved (cannot be: %s)" % (wiki_id, ", ".join(RESERVED_WIKI_IDS))
    return None


def read_wikis(instance_path):
    """Read wikis.yaml and return the list of wiki dicts."""
    path = wikis_yaml_path(instance_path)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("wikis", [])


def write_wikis(instance_path, wikis):
    """Write the wikis list to wikis.yaml."""
    path = wikis_yaml_path(instance_path)
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    data = {"wikis": wikis}
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def build_url(domain, wiki_path=None):
    """Build a wiki URL from domain and optional path."""
    if wiki_path:
        return "%s/%s" % (domain.rstrip("/"), wiki_path.lstrip("/"))
    return domain


def parse_url(url):
    """Parse a wiki URL into (server_name, path).

    Matches Go behavior: splits on first '/' only.
    """
    parts = url.split("/", 1)
    server = parts[0]
    path = "/" + parts[1] if len(parts) > 1 else ""
    return server, path


def get_wiki_ids(wikis):
    """Extract wiki IDs from wiki list."""
    return [w.get("id", "") for w in wikis]


def wiki_id_exists(wikis, wiki_id):
    """Check if a wiki ID already exists."""
    return wiki_id in get_wiki_ids(wikis)


def wiki_url_exists(wikis, domain, wiki_path=None):
    """Check if a wiki URL already exists."""
    url = build_url(domain, wiki_path)
    return any(w.get("url", "") == url for w in wikis)


def run_module():
    module_args = dict(
        instance_path=dict(type="str", required=True),
        state=dict(type="str", default="read",
                   choices=["read", "generate", "add", "remove", "query"]),
        wiki_id=dict(type="str", required=False),
        domain=dict(type="str", required=False),
        wiki_path=dict(type="str", required=False),
        site_name=dict(type="str", required=False),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True,
    )

    instance_path = module.params["instance_path"]
    state = module.params["state"]
    wiki_id = module.params.get("wiki_id")
    domain = module.params.get("domain")
    wiki_path = module.params.get("wiki_path")
    site_name = module.params.get("site_name")

    result = {"changed": False}

    if state == "read":
        wikis = read_wikis(instance_path)
        result["wikis"] = wikis
        result["wiki_ids"] = get_wiki_ids(wikis)

    elif state == "query":
        if not wiki_id:
            module.fail_json(msg="wiki_id is required for query")
            return
        wikis = read_wikis(instance_path)
        result["exists"] = wiki_id_exists(wikis, wiki_id)
        for w in wikis:
            if w.get("id") == wiki_id:
                result["wiki"] = w
                break

    elif state == "generate":
        if not wiki_id:
            module.fail_json(msg="wiki_id is required for generate")
            return
        if not domain:
            module.fail_json(msg="domain is required for generate")
            return
        err = validate_wiki_id(wiki_id)
        if err:
            module.fail_json(msg=err)
            return
        url = build_url(domain, wiki_path)
        name = site_name or wiki_id
        wikis = [{"id": wiki_id, "url": url, "name": name}]
        if not module.check_mode:
            write_wikis(instance_path, wikis)
        result["changed"] = True
        result["wikis"] = wikis

    elif state == "add":
        if not wiki_id:
            module.fail_json(msg="wiki_id is required for add")
            return
        if not domain:
            module.fail_json(msg="domain is required for add")
            return
        err = validate_wiki_id(wiki_id)
        if err:
            module.fail_json(msg=err)
            return
        wikis = read_wikis(instance_path)
        if wiki_id_exists(wikis, wiki_id):
            module.fail_json(msg="Wiki ID '%s' already exists" % wiki_id)
            return
        url = build_url(domain, wiki_path)
        if wiki_url_exists(wikis, domain, wiki_path):
            module.fail_json(msg="Wiki URL '%s' already exists" % url)
            return
        name = site_name or wiki_id
        wikis.append({"id": wiki_id, "url": url, "name": name})
        if not module.check_mode:
            write_wikis(instance_path, wikis)
        result["changed"] = True
        result["wikis"] = wikis

    elif state == "remove":
        if not wiki_id:
            module.fail_json(msg="wiki_id is required for remove")
            return
        wikis = read_wikis(instance_path)
        if not wiki_id_exists(wikis, wiki_id):
            module.fail_json(msg="Wiki ID '%s' not found" % wiki_id)
            return
        new_wikis = [w for w in wikis if w.get("id") != wiki_id]
        if not new_wikis:
            module.fail_json(msg="Cannot remove the last wiki")
            return
        if not module.check_mode:
            write_wikis(instance_path, new_wikis)
        result["changed"] = True
        result["wikis"] = new_wikis

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
