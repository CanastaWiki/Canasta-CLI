#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module to resolve a MediaWiki extension/skin git URL + branch.

Looks an extension/skin up in ExtensionJson.json (extjsonuploader Toolforge
dataset) to find its git repository URL, then selects the branch to check out:

- an explicit ``--branch`` wins;
- for Gerrit remotes (gerrit.wikimedia.org/r/mediawiki/...), the branch is
  ``REL1_YY`` derived from the instance's MediaWiki version (1.43.x -> REL1_43);
- otherwise the remote's default branch is used (no ``-b``).

The selected Gerrit REL branch is verified to exist via ``git ls-remote``;
if it does not, the branch falls back to the default (with a note) rather than
failing the clone.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_extension_resolve
short_description: Resolve an extension/skin git URL and branch from ExtensionJson.json
description:
  - Given an extension/skin name, find its git repository URL and the branch
    to check out for a given MediaWiki version.
options:
  name:
    description: Extension or skin name (matches an ExtensionJson.json key).
    type: str
    required: true
  item_type:
    description: Whether resolving an extension or skin (for future use).
    type: str
    choices: [extensions, skins]
    default: extensions
  mw_version:
    description: MediaWiki version (e.g. "1.43.2"). Drives REL1_YY for Gerrit remotes.
    type: str
  repository:
    description: Override the git URL (skips the ExtensionJson.json lookup).
    type: str
  branch:
    description: Override the branch/tag/commit to check out (skips auto selection).
    type: str
  json_path:
    description: Path to a local ExtensionJson.json snapshot (offline fallback).
    type: str
  json_url:
    description: URL of the live ExtensionJson.json.
    type: str
    default: https://extjsonuploader.toolforge.org/ExtensionJson.json
"""

import json
import re
import subprocess

from ansible.module_utils.basic import AnsibleModule

GERIT_RE = re.compile(
    r"gerrit\.wikimedia\.org(/r)?/mediawiki/(extensions|skins)/", re.IGNORECASE)
MW_VERSION_RE = re.compile(r"^1\.(\d+)(?:\.\d+)?$")
DEFAULT_JSON_URL = "https://extjsonuploader.toolforge.org/ExtensionJson.json"


def mw_minor_to_rel(mw_version):
    """Map '1.43.2' -> 'REL1_43'; return None if not a 1.x version."""
    if not mw_version:
        return None
    match = MW_VERSION_RE.match(mw_version.strip())
    if not match:
        return None
    return "REL1_%s" % match.group(1)


def select_branch(repository, mw_version, explicit_branch):
    """Return the branch to check out: explicit > Gerrit REL1_YY > None."""
    if explicit_branch:
        return explicit_branch
    if repository and GERIT_RE.search(repository):
        return mw_minor_to_rel(mw_version)
    return None


def branch_exists(repository, branch):
    """Return True if ``branch`` exists as a head in ``repository``."""
    if not branch:
        return False
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--heads", repository, branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        return bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def load_json(json_path, json_url):
    """Load ExtensionJson.json from the live URL, else a local snapshot.

    Precedence is live URL first (so the dataset stays current), falling back
    to a bundled/local snapshot when offline. Returns (data, source) where
    source is 'url:<url>', 'file:<path>', or None.
    """
    try:
        import urllib.request
        request = urllib.request.Request(
            json_url, headers={"User-Agent": "canasta-cli"})
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), "url:%s" % json_url
    except Exception:
        pass
    if json_path:
        try:
            with open(json_path) as handle:
                return json.load(handle), "file:%s" % json_path
        except (OSError, ValueError):
            pass
    return None, None


def resolve(name, item_type, mw_version, repository, branch, json_path, json_url):
    branch_note = None
    if repository:
        url = repository
        entry = None
        source = "explicit"
    else:
        data, source = load_json(json_path, json_url)
        if data is None:
            return {
                "failed": True,
                "msg": ("Could not load ExtensionJson.json (offline and no local "
                        "snapshot at %s). Connect to the network or pass "
                        "--repository." % json_path),
            }
        entry = data.get(name)
        if not entry:
            return {
                "failed": True,
                "msg": ("Extension/skin '%s' not found in ExtensionJson.json. "
                        "Check the spelling, or pass --repository to specify the "
                        "git URL." % name),
            }
        url = entry.get("repository")
        if not url:
            return {
                "failed": True,
                "msg": ("Extension/skin '%s' has no 'repository' in "
                        "ExtensionJson.json. Pass --repository to specify the "
                        "git URL." % name),
            }

    selected = select_branch(url, mw_version, branch)
    # A Gerrit REL branch may not exist for every extension; if it doesn't,
    # fall back to the default branch instead of failing the clone.
    if (selected and branch is None and GERIT_RE.search(url)
            and not branch_exists(url, selected)):
        branch_note = ("%s does not exist; using the default branch" % selected)
        selected = None

    result = {
        "name": name,
        "item_type": item_type,
        "repository": url,
        "branch": selected,
        "branch_note": branch_note,
        "source": source,
        "url": (entry or {}).get("url"),
        "description": (entry or {}).get("description"),
        "mw_required": (entry or {}).get("requires", {}).get("MediaWiki"),
    }
    return result


def run_module():
    module = AnsibleModule(
        argument_spec={
            "name": {"type": "str", "required": True},
            "item_type": {"type": "str",
                          "choices": ["extensions", "skins"],
                          "default": "extensions"},
            "mw_version": {"type": "str", "required": False},
            "repository": {"type": "str", "required": False},
            "branch": {"type": "str", "required": False},
            "json_path": {"type": "str", "required": False},
            "json_url": {"type": "str", "required": False,
                         "default": DEFAULT_JSON_URL},
        },
        supports_check_mode=True,
    )
    params = module.params
    try:
        result = resolve(
            params["name"], params.get("item_type", "extensions"),
            params.get("mw_version"), params.get("repository"),
            params.get("branch"), params.get("json_path"),
            params.get("json_url", DEFAULT_JSON_URL))
    except Exception as exc:  # pragma: no cover - defensive
        module.fail_json(msg="Error resolving extension/skin: %s" % exc)
    if result.get("failed"):
        module.fail_json(msg=result["msg"])
    module.exit_json(changed=False, **result)


def main():
    run_module()


if __name__ == "__main__":
    main()
