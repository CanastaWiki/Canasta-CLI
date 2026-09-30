#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module to resolve a MediaWiki extension/skin git URL + branch.

Finds the git repository URL of an extension or skin, then selects the branch
to check out. The URL comes from, in order:

- an explicit ``--repository``;
- ExtensionJson.json (extjsonuploader Toolforge dataset), matching the name
  case-insensitively; it covers extensions only, and an entry whose repository
  is not a usable git remote falls through;
- Wikimedia Gerrit (``mediawiki/extensions/<Name>`` or ``mediawiki/skins/<Name>``),
  matched case-insensitively; archived (read-only) projects are refused.

The branch is chosen as follows:

- an explicit ``--branch`` wins;
- otherwise the branch is ``REL1_YY`` derived from the instance's MediaWiki
  version (1.43.x -> REL1_43), attempted on every remote (GitHub mirrors of
  MediaWiki extensions carry the same REL branches);
- if that REL branch does not exist on the remote, the default branch is used
  (with a note) rather than failing the clone.

The selected REL branch is verified to exist via ``git ls-remote`` before it
is handed back.

URLs from ExtensionJson.json and Gerrit must be https remotes, so ``ext::``
transport strings, plaintext http, or leading-dash options cannot reach
``git``. An explicit ``--repository`` only has to pass the leading-dash check.
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
    description: Extension or skin name, matched case-insensitively.
    type: str
    required: true
  item_type:
    description: Whether resolving an extension or skin (for future use).
    type: str
    choices: [extensions, skins]
    default: extensions
  mw_version:
    description: MediaWiki version (e.g. "1.43.2"). Drives REL1_YY branch selection.
    type: str
  repository:
    description: Override the git URL (skips the ExtensionJson.json lookup).
    type: str
  branch:
    description: Override the branch/tag/commit to check out (skips auto selection).
    type: str
  json_path:
    description: Path to a local ExtensionJson.json snapshot (preferred when present).
    type: str
  json_url:
    description: URL of the live ExtensionJson.json (fallback when no snapshot).
    type: str
    default: https://extjsonuploader.toolforge.org/ExtensionJson.json
returns:
  name:
    description: The extension/skin name, in its canonical spelling.
    returned: success
    type: str
  item_type:
    description: Whether this is an extension or a skin (extensions or skins).
    returned: success
    type: str
  repository:
    description: The resolved git repository URL.
    returned: success
    type: str
  branch:
    description: Branch to check out; C(null) means the remote's default branch.
    returned: success
    type: str
  branch_note:
    description: Explanation when the requested REL branch was missing.
    returned: success
    type: str
  branch_unverified:
    description: >-
      True when the expected REL1_YY branch could not be verified from this
      host (e.g. no network to the remote); the branch is still used and the
      instance-side clone will fail loudly if it is actually absent, rather
      than silently falling back to the default branch.
    returned: success
    type: bool
  source:
    description: Where the data came from ('explicit', 'url:<url>', 'file:<path>', 'gerrit').
    returned: success
    type: str
  source_label:
    description: >-
      Human-readable origin of the repository URL ('--repository',
      'ExtensionJson.json' or 'Wikimedia Gerrit'), suffixed with
      '; not hosted on Wikimedia Gerrit' when the URL points elsewhere.
    returned: success
    type: str
  url:
    description: The extension's canonical page URL from ExtensionJson.json.
    returned: success
    type: str
  description:
    description: Short description from ExtensionJson.json.
    returned: success
    type: str
  mw_required:
    description: MediaWiki version constraint from ExtensionJson.json.
    returned: success
    type: str
"""

import json
import re
import subprocess

from ansible.module_utils.basic import AnsibleModule

MW_VERSION_RE = re.compile(r"^1\.(\d+)(?:\.\d+)?$")
DEFAULT_JSON_URL = "https://extjsonuploader.toolforge.org/ExtensionJson.json"
GERRIT_URL = "https://gerrit.wikimedia.org/r/"
GERRIT_HOST_RE = re.compile(r"^https://gerrit\.wikimedia\.org(?:/|$)", re.IGNORECASE)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def mw_minor_to_rel(mw_version):
    """Map '1.43.2' -> 'REL1_43'; return None if not a 1.x version."""
    if not mw_version:
        return None
    match = MW_VERSION_RE.match(mw_version.strip())
    if not match:
        return None
    return "REL1_%s" % match.group(1)


def select_branch(repository, mw_version, explicit_branch):
    """Return the branch to check out: explicit > REL1_YY > None."""
    if explicit_branch:
        return explicit_branch
    # Attempt REL1_YY on every remote, not just Gerrit: GitHub/GitLab mirrors
    # of MediaWiki extensions carry the same REL branches. Callers verify the
    # branch exists (branch_exists) and fall back to the default otherwise.
    return mw_minor_to_rel(mw_version)


def _validate_url_basic(url):
    """Cheap injection-safety gate applied to every URL, trusted or not.

    Refuses empty or leading-dash URLs, which git would read as a missing
    argument or an option rather than a remote. Scheme restrictions are left
    to validate_repository_url, which is applied only to untrusted lookups.
    """
    if not url:
        return "Empty repository URL."
    stripped = url.strip()
    if stripped.startswith("-"):
        return ("Refusing repository URL starting with '-': %s" % url)
    return None


def validate_repository_url(url):
    """Return an error message if an *untrusted* ``url`` must not reach git.

    Git accepts ``ext::sh -c ...`` transport URLs and treats leading-dash
    arguments as options; only https remotes are allowed for values that
    come from the community-supplied ExtensionJson.json or Gerrit. A trusted
    operator-provided ``--repository`` override skips this check (see
    resolve), so internal ``ssh://`` or ``git@host:path`` remotes work.
    """
    basic = _validate_url_basic(url)
    if basic:
        return basic
    low = url.strip().lower()
    if not low.startswith("https://"):
        return ("Refusing repository URL '%s': only https git remotes are "
                "supported." % url)
    return None


def branch_exists(repository, branch):
    """Return True/False if ``branch`` exists as a head in ``repository``.

    Returns ``None`` when the remote could not be reached (network, auth, or
    timeout error). Callers must treat ``None`` as "unknown", NOT as "absent",
    so an unverifiable branch is never silently treated as missing (which would
    otherwise fall through to the wrong default branch).
    """
    if not branch:
        return False
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--heads", repository, branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if out.returncode != 0:
            return None
        return bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def load_json(json_path, json_url):
    """Load ExtensionJson.json from a local snapshot, else the live URL.

    Precedence is the bundled/local snapshot first (instant and
    offline-safe; refresh it via 'make refresh-extension-json'), falling
    back to the live URL when no usable snapshot exists. Returns (data,
    source) where source is 'file:<path>', 'url:<url>', or None.
    """
    if json_path:
        try:
            with open(json_path) as handle:
                return json.load(handle), "file:%s" % json_path
        except (OSError, ValueError):
            pass
    try:
        import urllib.request
        request = urllib.request.Request(
            json_url, headers={"User-Agent": "canasta-cli"})
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), "url:%s" % json_url
    except Exception:
        pass
    return None, None


def find_entry(data, name):
    """Return (key, entry) for ``name``: an exact key, else the one key that
    matches case-insensitively. (None, None) when absent or ambiguous."""
    if name in data:
        return name, data[name]
    matches = [key for key in data if key.lower() == name.lower()]
    if len(matches) == 1:
        return matches[0], data[matches[0]]
    return None, None


def usable_dataset_url(url):
    """True if a dataset repository value can be cloned: an https remote,
    and not a Phabricator Diffusion URL, which no longer serves clones."""
    if not url or validate_repository_url(url):
        return False
    return "phabricator.wikimedia.org/" not in url.lower()


def source_label(source, url):
    """Describe where ``url`` came from for the operator, flagging a remote
    that is not on Wikimedia Gerrit."""
    if source == "explicit":
        label = "--repository"
    elif source == "gerrit":
        label = "Wikimedia Gerrit"
    else:
        label = "ExtensionJson.json"
    if not GERRIT_HOST_RE.match((url or "").strip()):
        label += "; not hosted on Wikimedia Gerrit"
    return label


def gerrit_projects(name):
    """Query Gerrit for projects whose name contains ``name`` (case-insensitive).

    Returns the decoded project map, or None when Gerrit could not be reached.
    """
    try:
        import urllib.parse
        import urllib.request
        url = "%sprojects/?m=%s&d" % (GERRIT_URL, urllib.parse.quote(name))
        request = urllib.request.Request(url, headers={"User-Agent": "canasta-cli"})
        with urllib.request.urlopen(request, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except Exception:
        return None
    # Gerrit prefixes JSON responses with )]}' to defeat XSSI.
    try:
        return json.loads(body.split("\n", 1)[1] if body.startswith(")]}'") else body)
    except ValueError:
        return None


def gerrit_lookup(item_type, name):
    """Find ``mediawiki/<item_type>/<name>`` on Gerrit, case-insensitively.

    Returns (status, canonical_name, detail) with status 'active' (detail is
    the clone URL), 'archived' (detail is the project description),
    'missing', or 'unreachable'.
    """
    projects = gerrit_projects(name)
    if projects is None:
        return "unreachable", name, None
    wanted = ("mediawiki/%s/%s" % (item_type, name)).lower()
    for project, info in projects.items():
        if project.lower() != wanted:
            continue
        canonical = project.rsplit("/", 1)[1]
        if (info or {}).get("state") == "ACTIVE":
            return "active", canonical, GERRIT_URL + project
        return "archived", canonical, " ".join(
            ((info or {}).get("description") or "").split())
    return "missing", name, None


def resolve(name, item_type, mw_version, repository, branch, json_path, json_url):
    branch_note = None
    branch_unverified = False
    if repository:
        url = repository
        entry = None
        source = "explicit"
    else:
        if not NAME_RE.match(name or ""):
            return {"failed": True,
                    "msg": "Invalid extension/skin name '%s'." % name}
        kind = "Skin" if item_type == "skins" else "Extension"
        url = None
        entry = None
        # ExtensionJson.json holds extension manifests only.
        data, source = (load_json(json_path, json_url)
                        if item_type == "extensions" else (None, None))
        if data is not None:
            key, entry = find_entry(data, name)
            if entry:
                name = key
                if usable_dataset_url(entry.get("repository")):
                    url = entry.get("repository")
        if url is None:
            status, canonical, detail = gerrit_lookup(item_type, name)
            if status == "active":
                name, url, source = canonical, detail, "gerrit"
            elif status == "archived":
                return {
                    "failed": True,
                    "msg": ("%s '%s' is archived on Wikimedia Gerrit%s. Pass "
                            "--repository with its current git URL."
                            % (kind, canonical,
                               " (%s)" % detail if detail else "")),
                }
            elif status == "unreachable":
                return {
                    "failed": True,
                    "msg": ("Could not reach Wikimedia Gerrit to look up %s "
                            "'%s'. Pass --repository with its git URL."
                            % (kind.lower(), name)),
                }
            elif item_type == "skins":
                return {
                    "failed": True,
                    "msg": ("Skin '%s' is not on Wikimedia Gerrit. Pass "
                            "--repository with its git URL." % name),
                }
            else:
                return {
                    "failed": True,
                    "msg": ("Extension '%s' was not found in ExtensionJson.json "
                            "or on Wikimedia Gerrit. Check the spelling, or pass "
                            "--repository with its git URL." % name),
                }

    # Always refuse injection-style URLs (empty / leading dash), regardless of
    # where the value came from.
    safety_error = _validate_url_basic(url)
    if safety_error:
        return {"failed": True, "msg": safety_error}
    # The ExtensionJson.json and Gerrit values are looked up, not chosen by
    # the operator, so they must be https remotes. An operator-provided --repository
    # override is trusted and may be an internal ssh:// or git@ host, so it
    # skips the scheme restriction.
    if repository is None:
        url_error = validate_repository_url(url)
        if url_error:
            return {"failed": True, "msg": url_error}

    selected = select_branch(url, mw_version, branch)
    # A REL branch may not exist for every extension (especially on remotes
    # that are not Wikimedia mirrors). If ls-remote can confirm it is absent we
    # fall back to the default branch; but if it could not be *verified* (e.g.
    # the host running this module has no network to the remote, while the
    # instance that will actually clone does), we keep the expected REL1_YY
    # branch and let the instance-side clone fail loudly rather than silently
    # checking out the wrong (default) branch.
    verified = branch_exists(url, selected) if selected else False
    if selected and branch is None:
        if verified is False:
            branch_note = ("%s does not exist; using the default branch" % selected)
            selected = None
        elif verified is None:
            branch_unverified = True
            branch_note = ("Could not verify %s exists (no network to the remote "
                          "from this host); using it anyway — the clone will fail "
                          "loudly if it is absent." % selected)

    result = {
        "name": name,
        "item_type": item_type,
        "repository": url,
        "branch": selected,
        "branch_note": branch_note,
        "branch_unverified": branch_unverified,
        "source": source,
        "source_label": source_label(source, url),
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
