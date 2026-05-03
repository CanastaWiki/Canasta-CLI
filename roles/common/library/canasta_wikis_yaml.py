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
    choices: [read, generate, add, remove, query, update_port, update_domain]
    default: read
  wiki_id:
    description: Wiki ID.
    type: str
  domain:
    description: Domain name for the wiki URL.
    type: str
  old_domain:
    description: >-
      With state=update_domain, only update wikis whose current host matches
      this value. Required for update_domain — prevents clobbering wikis that
      live on other domains in multi-domain instances.
    type: str
  wiki_path:
    description: URL path suffix (e.g. 'docs' for example.com/docs).
    type: str
  site_name:
    description: Display name of the wiki.
    type: str
"""

import os

import yaml

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_validate import (
    RESERVED_WIKI_IDS,
    validate_wiki_id,
)


def wikis_yaml_path(instance_path):
    """Return the path to wikis.yaml, validated against path traversal."""
    path = os.path.join(instance_path, "config", "wikis.yaml")
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(instance_path)
    if not real_path.startswith(real_base + os.sep) and real_path != real_base:
        raise ValueError(
            "wikis.yaml path '%s' escapes instance directory '%s'"
            % (real_path, real_base)
        )
    return path


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


def update_url_port(url, new_port, default_port="443"):
    """Update the port in a wiki URL.

    Strips existing port, adds new port unless it equals default_port.
    Preserves path component. default_port is "443" for HTTPS or "80" for HTTP.
    """
    domain = url
    path = ""
    slash_idx = url.find("/")
    if slash_idx != -1:
        domain = url[:slash_idx]
        path = url[slash_idx:]

    # Strip existing port
    colon_idx = domain.rfind(":")
    if colon_idx != -1:
        domain = domain[:colon_idx]

    # Add new port unless it's the default for the scheme
    if new_port != default_port:
        domain = "%s:%s" % (domain, new_port)

    return domain + path


def url_host(url):
    """Extract the bare host from a wiki URL, stripping port and path."""
    server, _ = parse_url(url)
    colon_idx = server.rfind(":")
    if colon_idx != -1:
        return server[:colon_idx]
    return server


def update_url_domain(url, new_domain):
    """Replace the domain in a wiki URL, preserving port and path."""
    old_domain = url
    path = ""
    slash_idx = url.find("/")
    if slash_idx != -1:
        old_domain = url[:slash_idx]
        path = url[slash_idx:]

    # Preserve existing port if any
    port = ""
    colon_idx = old_domain.rfind(":")
    if colon_idx != -1:
        port = old_domain[colon_idx:]

    # Strip port from new domain if it has one (caller provides bare domain)
    new_bare = new_domain
    nc = new_domain.rfind(":")
    if nc != -1:
        new_bare = new_domain[:nc]
        port = new_domain[nc:]

    return new_bare + port + path


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
                   choices=["read", "generate", "add", "remove", "query",
                            "update_port", "update_domain"]),
        wiki_id=dict(type="str", required=False),
        domain=dict(type="str", required=False),
        old_domain=dict(type="str", required=False),
        wiki_path=dict(type="str", required=False),
        site_name=dict(type="str", required=False),
        port=dict(type="str", required=False),
        default_port=dict(type="str", required=False, default="443"),
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
    port = module.params.get("port")
    default_port = module.params.get("default_port") or "443"

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

    elif state == "update_port":
        if not port:
            module.fail_json(msg="port is required for update_port")
            return
        wikis = read_wikis(instance_path)
        updated = []
        for w in wikis:
            w = dict(w)
            w["url"] = update_url_port(w.get("url", ""), port, default_port)
            updated.append(w)
        if not module.check_mode:
            write_wikis(instance_path, updated)
        result["changed"] = True
        result["wikis"] = updated

    elif state == "update_domain":
        if not domain:
            module.fail_json(msg="domain is required for update_domain")
            return
        old_domain = module.params.get("old_domain")
        if not old_domain:
            module.fail_json(
                msg="old_domain is required for update_domain "
                    "(filter prevents clobbering wikis on other domains)"
            )
            return
        # Strip port from old_domain for host-only comparison.
        old_colon = old_domain.rfind(":")
        old_host = old_domain[:old_colon] if old_colon != -1 else old_domain
        wikis = read_wikis(instance_path)
        updated = []
        changed = False
        for w in wikis:
            w = dict(w)
            url = w.get("url", "")
            if url_host(url) == old_host:
                new_url = update_url_domain(url, domain)
                if new_url != url:
                    w["url"] = new_url
                    changed = True
            updated.append(w)
        if changed and not module.check_mode:
            write_wikis(instance_path, updated)
        result["changed"] = changed
        result["wikis"] = updated

    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
