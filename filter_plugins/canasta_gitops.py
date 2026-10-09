# Jinja filters for the Compose gitops flow.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import re

import yaml

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

    ``base`` is the template as last applied to this host; it is what both
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


class FilterModule(object):
    def filters(self):
        return {
            "canasta_gitattributes_missing_rules":
                canasta_gitattributes_missing_rules,
            "canasta_wikis_template_ids": canasta_wikis_template_ids,
            "canasta_wikis_sync_action": canasta_wikis_sync_action,
            "canasta_wikis_render_with_live_urls":
                canasta_wikis_render_with_live_urls,
        }
