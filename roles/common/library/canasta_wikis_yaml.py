#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for managing Canasta wikis.yaml files.

Provides CRUD operations on the wikis.yaml file that defines wiki farm
configurations.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_wikis_yaml
short_description: Manage Canasta wikis.yaml
description:
  - Read, add, and remove wikis from the wikis.yaml farm configuration.
  - Operates on the wikis.yaml farm-configuration format.
options:
  instance_path:
    description: Path to the Canasta instance directory.
    type: str
    required: true
  state:
    description: Action to perform.
    type: str
    choices: [read, generate, add, remove, query, update_port, update_domain,
              add_database, remove_database]
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
  database:
    description: >-
      With state=add_database or state=remove_database, the extra database
      to include in (or drop from) the wiki's backup group.
    type: str
  port:
    description: With state=update_port, the new port for the wiki URL(s).
    type: str
  default_port:
    description: >-
      The scheme's default port ("443" for HTTPS, "80" for HTTP); the port is
      omitted from the URL when it equals this.
    type: str
    default: "443"
"""

import os

import yaml

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_validate import (
    validate_extra_database,
    validate_wiki_id,
)


def wikis_yaml_path(instance_path):
    """Return the path to wikis.yaml, validated against path traversal."""
    path = os.path.join(instance_path, "config", "wikis.yaml")
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(instance_path)
    if not real_path.startswith(real_base + os.sep):
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
    if not isinstance(data, dict):
        raise ValueError("top level is not a mapping")
    return data.get("wikis", [])


def write_wikis(instance_path, wikis):
    """Write the wikis list to wikis.yaml."""
    path = wikis_yaml_path(instance_path)
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
        os.chmod(directory, 0o755)
    data = {"wikis": wikis}
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    # The web container reads it as a different user; the operator's
    # umask must not decide whether it can.
    os.chmod(path, 0o644)


def build_url(domain, wiki_path=None):
    """Build a wiki URL from domain and optional path."""
    if wiki_path:
        return "%s/%s" % (domain.rstrip("/"), wiki_path.lstrip("/"))
    return domain


def parse_url(url):
    """Parse a wiki URL into (server_name, path).

    Splits on the first '/' only.
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


def extra_databases(wiki):
    """Return a wiki's declared extra database names.

    Entries are either a bare name or a mapping with a `name` key. The
    mapping form reserves room for a database that needs its own host or
    credential; that case cannot share a transaction with the wiki
    database (a dump is one connection), so it is rejected here rather
    than silently dumped at a different point in time.
    """
    declared = wiki.get("extra_databases") or []
    if not isinstance(declared, list):
        raise ValueError(
            "extra_databases for wiki '%s' must be a list" % wiki.get("id", "")
        )
    names = []
    for entry in declared:
        if isinstance(entry, dict):
            unsupported = sorted(k for k in entry if k != "name")
            if unsupported:
                raise ValueError(
                    "extra_databases entry '%s' for wiki '%s' sets %s. A "
                    "database reached with its own host or credential needs a "
                    "second connection, so it cannot be captured in the same "
                    "transaction as the wiki database; that is not supported "
                    "yet. Remove %s to back it up over the instance's own "
                    "database connection."
                    % (entry.get("name", entry), wiki.get("id", ""),
                       ", ".join(unsupported), ", ".join(unsupported))
                )
            name = entry.get("name")
        else:
            name = entry
        err = validate_extra_database(name)
        if err:
            raise ValueError(
                "wiki '%s': %s" % (wiki.get("id", ""), err)
            )
        names.append(name)
    return names


def get_db_groups(wikis):
    """Return the database groups to dump, one per wiki.

    Each group is dumped by a single `mariadb-dump --databases` call, so
    every database in it comes from one transaction. Cargo splits its
    state across the wiki database (`cargo_pages` and friends) and its
    own (`cargo__*`); capturing the two at different moments leaves rows
    that `cargo_pages` does not know about, which turns the next edit of
    those pages into duplicate rows instead of replacements.
    """
    groups = []
    for wiki in wikis:
        wiki_id = wiki.get("id", "")
        databases = [wiki_id] + [
            name for name in extra_databases(wiki) if name != wiki_id
        ]
        groups.append({"wiki": wiki_id, "databases": databases})
    return groups


def _require(module, state, *names):
    """Fail unless every named parameter is set; return True when all are."""
    for name in names:
        if not module.params.get(name):
            module.fail_json(msg="%s is required for %s" % (name, state))
            return False
    return True


def _read_or_fail(module, instance_path):
    try:
        return read_wikis(instance_path)
    except (yaml.YAMLError, ValueError) as exc:
        module.fail_json(msg="Failed to read %s: %s"
                         % (wikis_yaml_path(instance_path), exc))


def _write_if_changed(module, instance_path, wikis, changed):
    if changed and not module.check_mode:
        write_wikis(instance_path, wikis)


def _state_read(module, instance_path, state):
    result = {"changed": False}
    wikis = _read_or_fail(module, instance_path)
    result["wikis"] = wikis
    result["wiki_ids"] = get_wiki_ids(wikis)
    try:
        result["db_groups"] = get_db_groups(wikis)
    except ValueError as exc:
        module.fail_json(msg=str(exc))
        return None
    return result


def _state_query(module, instance_path, state):
    if not _require(module, state, "wiki_id"):
        return None
    wiki_id = module.params["wiki_id"]
    result = {"changed": False}
    wikis = _read_or_fail(module, instance_path)
    result["exists"] = wiki_id_exists(wikis, wiki_id)
    for w in wikis:
        if w.get("id") == wiki_id:
            result["wiki"] = w
            try:
                result["databases"] = get_db_groups([w])[0]["databases"]
            except ValueError as exc:
                module.fail_json(msg=str(exc))
                return None
            break
    return result


def _new_wiki_entry(module, state):
    """Validate the parameters of a new wiki and return its entry."""
    if not _require(module, state, "wiki_id", "domain"):
        return None
    wiki_id = module.params["wiki_id"]
    err = validate_wiki_id(wiki_id)
    if err:
        module.fail_json(msg=err)
        return None
    url = build_url(module.params["domain"], module.params.get("wiki_path"))
    name = module.params.get("site_name") or wiki_id
    return {"id": wiki_id, "url": url, "name": name}


def _state_generate(module, instance_path, state):
    entry = _new_wiki_entry(module, state)
    if entry is None:
        return None
    wikis = [entry]
    _write_if_changed(module, instance_path, wikis, True)
    return {"changed": True, "wikis": wikis}


def _state_add(module, instance_path, state):
    entry = _new_wiki_entry(module, state)
    if entry is None:
        return None
    wiki_id = entry["id"]
    wikis = _read_or_fail(module, instance_path)
    if wiki_id_exists(wikis, wiki_id):
        module.fail_json(msg="Wiki ID '%s' already exists" % wiki_id)
        return None
    if wiki_url_exists(wikis, module.params["domain"],
                       module.params.get("wiki_path")):
        module.fail_json(msg="Wiki URL '%s' already exists" % entry["url"])
        return None
    wikis.append(entry)
    _write_if_changed(module, instance_path, wikis, True)
    return {"changed": True, "wikis": wikis}


def _state_remove(module, instance_path, state):
    if not _require(module, state, "wiki_id"):
        return None
    wiki_id = module.params["wiki_id"]
    wikis = _read_or_fail(module, instance_path)
    if not wiki_id_exists(wikis, wiki_id):
        module.fail_json(msg="Wiki ID '%s' not found" % wiki_id)
        return None
    new_wikis = [w for w in wikis if w.get("id") != wiki_id]
    if not new_wikis:
        module.fail_json(msg="Cannot remove the last wiki")
        return None
    _write_if_changed(module, instance_path, new_wikis, True)
    return {"changed": True, "wikis": new_wikis}


def _state_database(module, instance_path, state):
    """Handle add_database and remove_database."""
    if not _require(module, state, "wiki_id", "database"):
        return None
    wiki_id = module.params["wiki_id"]
    database = module.params["database"]
    err = validate_extra_database(database)
    if err:
        module.fail_json(msg=err)
        return None
    wikis = _read_or_fail(module, instance_path)
    if not wiki_id_exists(wikis, wiki_id):
        module.fail_json(
            msg="Wiki ID '%s' not found (present: %s)"
                % (wiki_id, ", ".join(get_wiki_ids(wikis)))
        )
        return None
    updated = []
    changed = False
    for wiki in wikis:
        wiki = dict(wiki)
        if wiki.get("id") == wiki_id:
            try:
                current = extra_databases(wiki)
            except ValueError as exc:
                module.fail_json(msg=str(exc))
                return None
            if state == "add_database":
                if database == wiki_id:
                    module.fail_json(
                        msg="'%s' is the wiki's own database, which every "
                            "backup already includes" % database
                    )
                    return None
                if database not in current:
                    wiki["extra_databases"] = current + [database]
                    changed = True
            elif database in current:
                remaining = [n for n in current if n != database]
                if remaining:
                    wiki["extra_databases"] = remaining
                else:
                    wiki.pop("extra_databases", None)
                changed = True
        updated.append(wiki)
    _write_if_changed(module, instance_path, updated, changed)
    return {
        "changed": changed,
        "wikis": updated,
        "databases": get_db_groups(
            [w for w in updated if w.get("id") == wiki_id])[0]["databases"],
    }


def _state_update_port(module, instance_path, state):
    if not _require(module, state, "port"):
        return None
    port = module.params["port"]
    default_port = module.params.get("default_port") or "443"
    wikis = _read_or_fail(module, instance_path)
    updated = []
    changed = False
    for w in wikis:
        w = dict(w)
        old_url = w.get("url", "")
        new_url = update_url_port(old_url, port, default_port)
        if new_url != old_url:
            w["url"] = new_url
            changed = True
        updated.append(w)
    _write_if_changed(module, instance_path, updated, changed)
    return {"changed": changed, "wikis": updated}


def _state_update_domain(module, instance_path, state):
    if not _require(module, state, "domain"):
        return None
    domain = module.params["domain"]
    old_domain = module.params.get("old_domain")
    if not old_domain:
        module.fail_json(
            msg="old_domain is required for update_domain "
                "(filter prevents clobbering wikis on other domains)"
        )
        return None
    # Strip port from old_domain for host-only comparison.
    old_colon = old_domain.rfind(":")
    old_host = old_domain[:old_colon] if old_colon != -1 else old_domain
    wikis = _read_or_fail(module, instance_path)
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
    _write_if_changed(module, instance_path, updated, changed)
    return {"changed": changed, "wikis": updated}


STATE_HANDLERS = {
    "read": _state_read,
    "query": _state_query,
    "generate": _state_generate,
    "add": _state_add,
    "remove": _state_remove,
    "add_database": _state_database,
    "remove_database": _state_database,
    "update_port": _state_update_port,
    "update_domain": _state_update_domain,
}


def run_module():
    module_args = dict(
        instance_path=dict(type="str", required=True),
        state=dict(type="str", default="read",
                   choices=["read", "generate", "add", "remove", "query",
                            "update_port", "update_domain",
                            "add_database", "remove_database"]),
        wiki_id=dict(type="str", required=False),
        domain=dict(type="str", required=False),
        old_domain=dict(type="str", required=False),
        wiki_path=dict(type="str", required=False),
        site_name=dict(type="str", required=False),
        database=dict(type="str", required=False),
        port=dict(type="str", required=False),
        default_port=dict(type="str", required=False, default="443"),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True,
    )

    state = module.params["state"]
    result = STATE_HANDLERS[state](
        module, module.params["instance_path"], state)
    if result is None:
        return
    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
