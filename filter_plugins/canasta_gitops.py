# Jinja filters for the gitops flows.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import hashlib
import json
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "roles", "common", "module_utils"))

from canasta_gitops_pending import (  # noqa: E402
    unpushed_changes,
    unpushed_probe_script,
)
from canasta_env_keys import (  # noqa: E402
    PLAIN_HTTP_KEY,
    RENAMED_ENV_KEYS,
    modernize_text as canasta_env_modernize,
)

ENCRYPTION_FILTER = "git-crypt"

# wikis.yaml.template is not parseable YAML (its urls are {{wiki_url_<id>}}
# placeholders), but its writer emits each wiki as a column-0 "- id:" line.
_TEMPLATE_WIKI_ID = re.compile(r"""^- id:\s*["']?([^"'\s]+)""", re.MULTILINE)
_URL_LINE = re.compile(r"^[ \t]*url:.*$", re.MULTILINE)
_URL_PLACEHOLDER = re.compile(r"\{\{\s*wiki_url_([^\s}]+)\s*\}\}")


def _rules(content):
    """Yield (pattern, attributes, line) for each rule line of a .gitattributes."""
    for raw in str(content or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("[attr]"):
            continue
        fields = line.split()
        # A leading slash anchors to the file's directory, which is what a
        # pattern containing a slash already does.
        yield fields[0].lstrip("/"), fields[1:], line


def _filter_values(content):
    """Map each pattern to the filter value its lines leave in effect.

    Git applies a pattern's lines in order, so a later `-filter`, `!filter`,
    or `filter=other` on the same pattern undoes an earlier `filter=git-crypt`.
    """
    effective = {}
    for pattern, attrs, _line in _rules(content):
        for attr in attrs:
            if attr.startswith("filter="):
                effective[pattern] = attr.split("=", 1)[1]
            elif attr in ("filter", "-filter", "!filter"):
                effective[pattern] = None
    return effective


def canasta_gitattributes_missing_rules(content, required_content):
    """Return the encryption rules of ``required_content`` that ``content`` lacks.

    Each rule in ``required_content`` that sets filter=git-crypt is required;
    ``content`` satisfies it when the same pattern ends up with
    filter=git-crypt, whatever else the file holds. The result is the
    required lines themselves, so they can be shown as the text to restore.
    """
    have = _filter_values(content)
    missing = []
    for pattern, attrs, line in _rules(required_content):
        if "filter=" + ENCRYPTION_FILTER not in attrs:
            continue
        if have.get(pattern) != ENCRYPTION_FILTER:
            missing.append(line)
    return missing


def canasta_wikis_template_ids(content):
    """Return the wiki IDs declared in a wikis.yaml.template, in order."""
    return _TEMPLATE_WIKI_ID.findall(str(content or ""))


def _wikis_fields(content):
    """Map each wiki id to its fields other than url; None if unparseable.

    url is host-specific (a placeholder in the template), so it is left out
    of every comparison between the template and the live file.
    """
    try:
        doc = yaml.safe_load(_URL_LINE.sub("", str(content or ""))) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    out = {}
    for w in doc.get("wikis") or []:
        if isinstance(w, dict) and "id" in w:
            out[str(w["id"])] = {k: v for k, v in w.items() if k != "url"}
    return out


def canasta_wikis_sync_action(live, template, base):
    """Decide which way to sync config/wikis.yaml and wikis.yaml.template.

    ``base`` is the template as staged in the git index; it is what both
    files agreed on before either was edited. Returns "none", "render"
    (template was edited), "capture" (live file was edited), or "conflict"
    (both were edited and disagree). With no ``base`` the live file wins.
    """
    live_f = _wikis_fields(live)
    tmpl_f = _wikis_fields(template)
    if live_f is None or tmpl_f is None or live_f == tmpl_f:
        return "none"
    base_f = _wikis_fields(base) if str(base or "").strip() else None
    if base_f is None:
        return "capture"
    if live_f == base_f:
        return "render"
    if tmpl_f == base_f:
        return "capture"
    return "conflict"


def canasta_wikis_render_with_live_urls(template, live):
    """Render a wikis.yaml.template using each wiki's url from the live file.

    Raises when the template names a wiki the live file has no url for, so
    a wiki is never written with a blank url.
    """
    try:
        doc = yaml.safe_load(str(live or "")) or {}
    except yaml.YAMLError:
        doc = {}
    wikis = (doc.get("wikis") if isinstance(doc, dict) else None) or []
    urls = {
        str(w["id"]): w["url"]
        for w in wikis
        if isinstance(w, dict) and w.get("id") is not None and w.get("url")
    }
    missing = [i for i in _URL_PLACEHOLDER.findall(str(template or ""))
               if i not in urls]
    if missing:
        raise ValueError(
            "wikis.yaml.template names wiki(s) %s that config/wikis.yaml has "
            "no url for; add a wiki with 'canasta add' rather than by editing "
            "the template" % ", ".join(missing))
    return _URL_PLACEHOLDER.sub(lambda m: str(urls[m.group(1)]),
                                str(template or ""))


_ABSENT = object()


def _refresh_values(values, base, shipped, former, path):
    out = {}
    for key in list(values) + [k for k in shipped if k not in values]:
        here = "%s.%s" % (path, key) if path else str(key)
        mine = values.get(key, _ABSENT)
        old = base.get(key, _ABSENT) if isinstance(base, dict) else _ABSENT
        new = shipped.get(key, _ABSENT)
        if isinstance(mine, dict) and isinstance(new, dict):
            out[key] = _refresh_values(
                mine, old if isinstance(old, dict) else {}, new, former, here)
        elif mine is _ABSENT:
            out[key] = new
        elif mine == old or mine in former.get(here, []):
            if new is not _ABSENT:
                out[key] = new
        else:
            out[key] = mine
    return out


def canasta_chart_values_refresh(values, shipped, base=None, former=None,
                                 keep=None):
    """Bring a gitops repo's values.yaml up to date with the shipped chart.

    The repo's values.yaml carries a full copy of the chart defaults, because
    Argo CD renders the repo itself as the chart. A value still equal to the
    default the repo last took (`base`), or to a former default listed in
    `former` (dotted path -> values), follows the shipped default, and is
    dropped when the chart no longer has it. Anything else is the operator's
    and is kept. Keys the chart added are filled in. Lists are compared whole.
    Top-level keys in `keep` are left exactly as they are, or absent.
    """
    values = values or {}
    keep = set(keep or [])
    out = _refresh_values(values, base or {}, shipped or {}, former or {}, "")
    for key in keep:
        out.pop(key, None)
        if key in values:
            out[key] = values[key]
    return out


def canasta_env_render(template, env_vars):
    """Render env.template with a host's placeholder values.

    A `KEY={{name}}` line takes the value of `name`; one with no value, or
    a null, is left out rather than written as `KEY=`, so the reader's own
    default applies. Every other line is copied as is.
    """
    env_vars = env_vars or {}
    out = []
    for line in str(template or "").split("\n"):
        if "={{" in line and "}}" in line:
            key = line.split("=", 1)[0]
            placeholder = line.split("={{", 1)[1].split("}}", 1)[0]
            value = env_vars.get(placeholder, "")
            if value is not None and value != "":
                out.append("%s=%s" % (key, value))
        else:
            out.append(line)
    return canasta_env_modernize("".join(line + "\n" for line in out))


def _template_keys(template):
    keys = set()
    for line in str(template or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            keys.add(stripped.split("=", 1)[0].strip())
    return keys


def canasta_env_legacy_settings(settings, template):
    """Name each KEY=VALUE setting as env.template does.

    A renamed key the template still has under its old name is stored
    under that name; TLS_MODE=off is stored as CADDY_AUTO_HTTPS=off where
    the template has that line.
    """
    keys = _template_keys(template)
    old_name = {new: old for old, new in RENAMED_ENV_KEYS.items()}
    out = []
    for setting in settings or []:
        key, _, value = str(setting).partition("=")
        if key not in old_name or key in keys:
            out.append(setting)
            continue
        if key == "TLS_MODE" and PLAIN_HTTP_KEY in keys:
            if value.strip().lower() == "off":
                out.append("%s=off" % PLAIN_HTTP_KEY)
                continue
            out.append("%s=on" % PLAIN_HTTP_KEY)
        out.append("%s=%s" % (old_name[key] if old_name[key] in keys else key, value))
    return out


def canasta_env_legacy_keys(names, template):
    """The env.template keys that hold each of the given .env keys."""
    keys = _template_keys(template)
    old_name = {new: old for old, new in RENAMED_ENV_KEYS.items()}
    out = []
    for key in names or []:
        held = [key] if key in keys or key not in old_name else []
        if key in old_name and old_name[key] in keys:
            held.append(old_name[key])
        if key == "TLS_MODE" and PLAIN_HTTP_KEY in keys:
            held.append(PLAIN_HTTP_KEY)
        out.extend(k for k in held if k not in out)
    return out


def _env_values(content):
    """Map each KEY of a .env-style text to its raw value."""
    values = {}
    for line in str(content or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


# .env keys the CLI re-derives from the feature flags on every start; a
# difference in them is never an operator edit.
CLI_DERIVED_ENV_KEYS = ("COMPOSE_PROFILES", "CANASTA_CADDY_IMAGE",
                        "CANASTA_CADDY_HTTP_PUBLISH",
                        "CANASTA_CADDY_HTTPS_PUBLISH")


def canasta_env_sync_plan(live, template, env_vars, base_template=None,
                          base_vars=None, ignore=CLI_DERIVED_ENV_KEYS):
    """Decide how to bring .env and its gitops sources into agreement.

    Each key is compared across the live .env, the render of the current
    sources, and the render of the sources as staged (``base_*``), which is
    what .env and the sources agreed on before either was edited.
    Returns ``set`` (live values to write into the sources), ``unset``
    (keys deleted from .env) and ``conflict`` (keys edited on both sides
    to different values). A key edited only in the sources needs nothing
    here: rendering .env afterwards applies it. With no base, the live
    file wins. Keys in ``ignore`` are derived by the CLI and never
    compared.
    """
    now = _env_values(canasta_env_render(template, env_vars))
    live_values = _env_values(live)
    if str(base_template or "").strip():
        base = _env_values(canasta_env_render(base_template, base_vars))
    else:
        base = now
    plan = {"set": {}, "unset": [], "conflict": []}
    for key in sorted((set(now) | set(live_values) | set(base))
                      - set(ignore or ())):
        mine, current, old = (live_values.get(key), now.get(key),
                              base.get(key))
        if mine == current or mine == old:
            continue
        if current == old:
            if mine is None:
                plan["unset"].append(key)
            else:
                plan["set"][key] = mine
        else:
            plan["conflict"].append(key)
    return plan


def canasta_wiki_url_sync_plan(live_wikis, env_vars, base_vars=None):
    """Decide which config/wikis.yaml urls to write into the host vars.

    On a gitops instance each wiki's url is rendered from its
    ``wiki_url_<id>`` var, which wikis.yaml.template references. A url
    edited only in config/wikis.yaml goes into the vars (``set``, a list of
    ``{id, url}``); one edited only in the vars is left for the render;
    edits to both that disagree are a ``conflict``. ``base_vars`` are the
    vars as staged; with none, the live file wins.
    """
    try:
        doc = yaml.safe_load(str(live_wikis or "")) or {}
    except yaml.YAMLError:
        doc = {}
    wikis = (doc.get("wikis") if isinstance(doc, dict) else None) or []
    env_vars = env_vars or {}
    base_vars = env_vars if base_vars is None else base_vars
    plan = {"set": [], "conflict": []}
    for wiki in wikis:
        if not isinstance(wiki, dict) or not wiki.get("id") or not wiki.get("url"):
            continue
        key = "wiki_url_%s" % wiki["id"]
        mine, current, old = (str(wiki["url"]), env_vars.get(key),
                              base_vars.get(key))
        if mine == current or mine == old:
            continue
        if current == old:
            plan["set"].append({"id": str(wiki["id"]), "url": mine})
        else:
            plan["conflict"].append(str(wiki["id"]))
    return plan


def canasta_wiki_ingress_domains(wikis_yaml, extra=None, fallback=None):
    """The Ingress domains for a farm: each wiki url's host, then ``extra``.

    The path and any port are dropped from each url, and duplicates are
    removed keeping the first. With no wiki urls (no or empty
    config/wikis.yaml), ``fallback`` is returned unchanged.
    """
    try:
        doc = yaml.safe_load(str(wikis_yaml or "")) or {}
    except yaml.YAMLError:
        doc = {}
    wikis = (doc.get("wikis") if isinstance(doc, dict) else None) or []
    hosts = []
    for wiki in wikis:
        if not isinstance(wiki, dict) or not wiki.get("url"):
            continue
        host = re.sub(r":[0-9]+$", "", str(wiki["url"]).split("/", 1)[0])
        if host:
            hosts.append(host)
    if not hosts:
        return list(fallback or [])
    domains = []
    for name in hosts + list(extra or []):
        if name not in domains:
            domains.append(name)
    return domains


# ssh/forge responses that mean the key was presented and refused. GitHub
# answers "Repository not found" for a key registered on another repo.
_REMOTE_UNAUTHORIZED = (
    "Permission denied (publickey",
    "Repository not found",
    "could not be found or you don't have permission",
)
_REMOTE_HOST_KEY = (
    "REMOTE HOST IDENTIFICATION HAS CHANGED",
    "Host key verification failed",
)


def canasta_git_remote_failure(stderr):
    """Classify a failed ``git ls-remote`` over ssh by its stderr.

    Returns ``host_key`` when ssh could not verify the server's host key,
    ``unauthorized`` when the server refused the key, and ``other`` for
    anything else (DNS, connection, a bad URL).
    """
    text = str(stderr or "")
    if any(marker in text for marker in _REMOTE_HOST_KEY):
        return "host_key"
    if any(marker in text for marker in _REMOTE_UNAUTHORIZED):
        return "unauthorized"
    return "other"


def canasta_git_remote_known_hosts_name(repo):
    """The name ssh records ``repo``'s server under in known_hosts.

    ``host`` for the default port, ``[host]:port`` otherwise, which is the
    form ``ssh-keygen -R`` takes. Empty when ``repo`` is not an ssh URL.
    """
    repo = str(repo or "")
    match = re.match(r"^ssh://(?:[^@/]*@)?(\[[^\]]+\]|[^:/]+)(?::([0-9]+))?/",
                     repo)
    if match:
        host, port = match.group(1).strip("[]"), match.group(2)
        return "[%s]:%s" % (host, port) if port and port != "22" else host
    if "://" in repo:
        return ""
    match = re.match(r"^(?:[^@/:]*@)?([^/:]+):", repo)
    return match.group(1) if match else ""


def _secret_fingerprint(data, refs):
    """sha256 over the (Secret, key, value) of each reference, in order."""
    rows = sorted([name, key, data.get(name, {}).get(key)] for name, key in refs)
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def canasta_secret_checksums(secrets, values, instance_id):
    """Fingerprint the Secret values each workload of an instance reads.

    ``secrets`` is a k8s_info Secret listing, ``values`` the instance's Helm
    values. Returns hashes only, never the values: ``web`` (database
    passwords, MediaWiki secret key and the --web app secrets), ``caddy``
    (the CrowdSec bouncer key and agent password), ``crowdsec`` (the agent
    password the local API registers) and ``sidecars`` (one per sidecar that
    reads app secrets), for the chart to put on each pod template so a
    changed Secret value rolls the pods that read it.
    """
    values = values or {}
    names = values.get("secrets") or {}
    db = names.get("dbSecretName") or "%s-db-credentials" % instance_id
    mw = names.get("mwSecretName") or "%s-mw-secrets" % instance_id
    app = names.get("appSecretName") or "%s-app-secrets" % instance_id
    crowdsec = ("canasta-%s" % instance_id)[:63].rstrip("-") + "-crowdsec"
    data = {}
    for secret in secrets or []:
        name = (secret.get("metadata") or {}).get("name")
        if name:
            data[name] = secret.get("data") or {}
    # The hashes are committed to git. MediaWiki's random secret key goes
    # into each one, so a guessable app secret can't be recovered from it.
    pepper = [(mw, "MW_SECRET_KEY")]
    # With its own account on the bundled database, MediaWiki reads that
    # account's password, and the web pod's ensure-db-account init container
    # also reads the root password.
    db_values = values.get("db") or {}
    own_account = (db_values.get("enabled", True) is not False
                   and str(db_values.get("wikiUser") or "root") != "root")
    if own_account:
        web = pepper + [(db, "WIKI_DB_PASSWORD"), (db, "MYSQL_ROOT_PASSWORD")]
    else:
        web = pepper + [(db, "MYSQL_PASSWORD")]
    web += [(app, key) for key in values.get("appSecretEnv") or []]
    sidecars = {}
    for sidecar in values.get("sidecars") or []:
        keys = sidecar.get("envSecret") or []
        if keys:
            sidecars[sidecar["name"]] = _secret_fingerprint(
                data, pepper + [(app, key) for key in keys])
    return {
        "web": _secret_fingerprint(data, web),
        "caddy": _secret_fingerprint(
            data, pepper + [(crowdsec, "CROWDSEC_BOUNCER_API_KEY"),
                            (crowdsec, "CROWDSEC_AGENT_PASSWORD")]),
        "crowdsec": _secret_fingerprint(
            data, pepper + [(crowdsec, "CROWDSEC_AGENT_PASSWORD")]),
        "sidecars": sidecars,
    }


# Fingerprint keys of the files a pull renders, and the path each names.
_RENDERED_FILES = (
    ("env", ".env"),
    ("wikis", "config/wikis.yaml"),
    ("mycnf", "my.cnf"),
    ("media_hosts", "config/media-hosts.yaml"),
)


def canasta_fingerprint_changes(baseline, current):
    """The files whose fingerprint differs between ``baseline`` and ``current``.

    Both are _rendered_fingerprint results. A key or file the baseline has no
    entry for was recorded before it was fingerprinted, so it is not a change;
    .env and wikis.yaml are in every baseline, and a missing one means the
    file did not exist.
    """
    baseline = baseline or {}
    current = current or {}
    changed = []
    for key, path in _RENDERED_FILES:
        default = "" if key in ("env", "wikis") else current.get(key, "")
        if baseline.get(key, default) != current.get(key, ""):
            changed.append(path)
    if "files" in baseline:
        before = baseline.get("files") or {}
        after = current.get("files") or {}
        changed.extend(sorted(p for p in set(before) | set(after)
                              if before.get(p) != after.get(p)))
    return changed


_SETTINGS_YAML = re.compile(
    r"^config/settings/(global|wikis/[^/]+)/settings\.yaml$")
_GITLINK_MODE = "160000"


def canasta_maintenance_triggers(raw_diff):
    """What in a ``git diff --raw`` needs MediaWiki's update.php to run.

    An extension or skin that was added, removed, or moved to another
    version can bring schema changes: a submodule pointer change, its
    extension.json or skin.json, or its SQL files. A changed settings.yaml
    can load an extension that was not loaded before. Returns the
    extensions/<name>, skins/<name> and settings.yaml paths, in order.
    """
    names = []
    for line in raw_diff or []:
        meta, _, path = str(line).partition("\t")
        if not meta.startswith(":") or not path:
            continue
        parts = path.split("/")
        if parts[0] in ("extensions", "skins") and len(parts) >= 2:
            gitlink = _GITLINK_MODE in meta[1:].split()[:2]
            registration = (len(parts) == 3
                            and parts[2] in ("extension.json", "skin.json"))
            schema = "sql" in parts[2:-1] or path.endswith(".sql")
            name = "/".join(parts[:2])
            if (gitlink or registration or schema) and name not in names:
                names.append(name)
        elif _SETTINGS_YAML.match(path) and path not in names:
            names.append(path)
    return names


def canasta_gitops_unpushed_probe(instance_path):
    """Read-only shell that gathers what a gitops instance has not pushed."""
    return unpushed_probe_script(instance_path)


def canasta_gitops_unpushed(stdout, secret_key_regex):
    """The unpushed commits and paths canasta_gitops_unpushed_probe found."""
    return unpushed_changes(stdout, secret_key_regex)


class FilterModule(object):
    def filters(self):
        return {
            "canasta_gitattributes_missing_rules":
                canasta_gitattributes_missing_rules,
            "canasta_wikis_template_ids": canasta_wikis_template_ids,
            "canasta_wikis_sync_action": canasta_wikis_sync_action,
            "canasta_wikis_render_with_live_urls":
                canasta_wikis_render_with_live_urls,
            "canasta_chart_values_refresh": canasta_chart_values_refresh,
            "canasta_env_render": canasta_env_render,
            "canasta_env_modernize": canasta_env_modernize,
            "canasta_env_legacy_settings": canasta_env_legacy_settings,
            "canasta_env_legacy_keys": canasta_env_legacy_keys,
            "canasta_env_sync_plan": canasta_env_sync_plan,
            "canasta_wiki_url_sync_plan": canasta_wiki_url_sync_plan,
            "canasta_wiki_ingress_domains": canasta_wiki_ingress_domains,
            "canasta_git_remote_failure": canasta_git_remote_failure,
            "canasta_git_remote_known_hosts_name":
                canasta_git_remote_known_hosts_name,
            "canasta_secret_checksums": canasta_secret_checksums,
            "canasta_gitops_unpushed_probe": canasta_gitops_unpushed_probe,
            "canasta_gitops_unpushed": canasta_gitops_unpushed,
            "canasta_fingerprint_changes": canasta_fingerprint_changes,
            "canasta_maintenance_triggers": canasta_maintenance_triggers,
        }
